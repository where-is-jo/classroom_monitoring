"""실제 카메라 source 등록 API 계약 테스트.

MongoDB mode에는 demo seed가 돌지 않아(seed는 memory 전용) camera_id를 원장에
넣을 수단이 이 엔드포인트뿐이다. 등록되지 않은 camera_id로 탐지 이벤트가 오면
student_monitoring이 404로 거절하므로, 여기서 막히면 탐지 결과가 화면에 닿지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.shared.dependencies import get_video_stream_service
from app.video_monitoring.adapters.memory_repository import MemoryVideoStreamRepository
from app.video_monitoring.service import VideoStreamService

from .fakes import FakeClock, make_classroom_service, make_stream

ENDPOINT = "/api/v1/video-streams"


@pytest.fixture
def repository() -> MemoryVideoStreamRepository:
    return MemoryVideoStreamRepository()


@pytest.fixture
def client(repository: MemoryVideoStreamRepository) -> Iterator[TestClient]:
    service = VideoStreamService(
        repository,
        make_classroom_service(),
        stale_seconds=300,
        clock=FakeClock(),
    )
    app.dependency_overrides[get_video_stream_service] = lambda: service
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "camera_id": "camera-02",
        "classroom_id": "classroom-a101",
        "camera_label": "A101 후면 카메라",
    }
    payload.update(overrides)
    return payload


def test_등록에_성공하면_201과_생성된_source를_돌려준다(
    client: TestClient, repository: MemoryVideoStreamRepository
) -> None:
    response = client.post(ENDPOINT, json=_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["camera_id"] == "camera-02"
    assert body["classroom_id"] == "classroom-a101"
    assert body["is_demo"] is False
    # 아직 프레임을 받은 적이 없다. 등록만으로 CONNECTED가 되면 안 된다.
    assert body["status"] == "UNKNOWN"
    assert body["last_frame_at"] is None
    assert repository.find_by_camera_id("camera-02") is not None


def test_재생_경로는_요청이_아니라_camera_id로_조립된다(client: TestClient) -> None:
    """WHEP proxy 대상이 외부 입력이 되지 않게 한다."""
    response = client.post(ENDPOINT, json=_payload(camera_id="camera-09"))

    assert response.status_code == 201
    assert response.json()["playback_path"] == "/webrtc/camera-09"
    assert response.json()["playback_kind"] == "WEBRTC"


def test_같은_camera_id는_409로_거절한다(
    client: TestClient, repository: MemoryVideoStreamRepository
) -> None:
    """이미 도는 카메라의 강의실 배정이 조용히 바뀌지 않게 한다."""
    repository.save(make_stream(stream_id="stream-01", camera_id="camera-01"))

    response = client.post(ENDPOINT, json=_payload(camera_id="camera-01"))

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VIDEO_STREAM_ALREADY_EXISTS"


def test_없는_강의실을_참조하면_404를_돌려준다(client: TestClient) -> None:
    response = client.post(ENDPOINT, json=_payload(classroom_id="classroom-없음"))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "CLASSROOM_NOT_FOUND"


@pytest.mark.parametrize(
    "overrides",
    [
        {"camera_id": ""},
        {"camera_label": ""},
        {"classroom_id": ""},
        {"playback_path": "/webrtc/침입"},
    ],
    ids=["빈_camera_id", "빈_라벨", "빈_강의실", "허용되지_않은_필드"],
)
def test_잘못된_입력은_422로_거절한다(client: TestClient, overrides: dict[str, object]) -> None:
    response = client.post(ENDPOINT, json=_payload(**overrides))

    assert response.status_code == 422


def test_등록한_source는_목록에서_조회된다(client: TestClient) -> None:
    client.post(ENDPOINT, json=_payload())

    listed = client.get(ENDPOINT)

    assert listed.status_code == 200
    camera_ids = [item.get("camera_id") for item in listed.json()["items"]]
    assert "camera-02" in camera_ids
