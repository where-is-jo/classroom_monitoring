"""영상 source 상세 조회의 식별자 계약 테스트.

목록이 돌려준 `id`로 상세를 다시 조회하는 것이 기본 흐름인데, 상세 조회만
`camera_id`로 찾고 있어 실제 source가 404가 되던 문제를 고정한다.
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

STREAM_ID = "stream-01"
CAMERA_ID = "camera-01"


@pytest.fixture
def client() -> Iterator[TestClient]:
    repository = MemoryVideoStreamRepository()
    repository.save(make_stream(stream_id=STREAM_ID, camera_id=CAMERA_ID))
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


def test_detail_is_reachable_by_the_id_the_list_returns(client: TestClient) -> None:
    """목록이 준 id를 그대로 상세에 넣으면 같은 source가 나와야 한다."""
    listed = client.get("/api/v1/video-streams").json()["items"]
    real = next(item for item in listed if item.get("camera_id") == CAMERA_ID)

    response = client.get(f"/api/v1/video-streams/{real['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == STREAM_ID
    assert response.json()["camera_id"] == CAMERA_ID
    assert response.json()["role"] == "SEAT_JUDGING"


def test_detail_still_accepts_camera_id(client: TestClient) -> None:
    """기존 호출자가 쓰던 camera_id 조회도 계속 동작한다."""
    response = client.get(f"/api/v1/video-streams/{CAMERA_ID}")

    assert response.status_code == 200
    assert response.json()["id"] == STREAM_ID


def test_unknown_stream_reports_video_stream_error_not_demo(client: TestClient) -> None:
    """실제 source 조회 실패를 데모 오류로 보고하지 않는다."""
    response = client.get("/api/v1/video-streams/no-such-source")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "VIDEO_STREAM_NOT_FOUND"
