from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.entry_identity_events.adapters.memory import (
    InMemoryEntryIdentityEventRepository,
)
from app.entry_identity_events.service import EntryIdentityEventService
from app.main import app
from app.shared.dependencies import get_entry_identity_event_service
from app.video_monitoring.adapters.memory_repository import MemoryVideoStreamRepository
from app.video_monitoring.models import CameraRole, PlaybackKind, VideoStream


@pytest.fixture
def client_and_clock() -> Iterator[tuple[TestClient, list[datetime]]]:
    clock = [datetime(2026, 8, 24, 9, 0, tzinfo=UTC)]
    streams = MemoryVideoStreamRepository()
    streams.save(
        VideoStream(
            id="entry-stream",
            camera_id="entry-camera",
            classroom_id="classroom-1",
            camera_label="입구",
            playback_kind=PlaybackKind.WEBRTC,
            playback_path="/webrtc/entry-camera",
            enabled=True,
            last_frame_at=None,
            last_detection_at=None,
            is_demo=False,
            created_at=clock[0],
            updated_at=clock[0],
            role=CameraRole.IDENTITY_ONLY,
        )
    )
    streams.save(
        VideoStream(
            id="cctv-stream",
            camera_id="classroom-cctv",
            classroom_id="classroom-1",
            camera_label="교실",
            playback_kind=PlaybackKind.WEBRTC,
            playback_path="/webrtc/classroom-cctv",
            enabled=True,
            last_frame_at=None,
            last_detection_at=None,
            is_demo=False,
            created_at=clock[0],
            updated_at=clock[0],
            role=CameraRole.SEAT_JUDGING,
        )
    )
    streams.save(
        VideoStream(
            id="inactive-entry-stream",
            camera_id="inactive-entry-camera",
            classroom_id="classroom-1",
            camera_label="비활성 입구",
            playback_kind=PlaybackKind.WEBRTC,
            playback_path="/webrtc/inactive-entry-camera",
            enabled=False,
            last_frame_at=None,
            last_detection_at=None,
            is_demo=False,
            created_at=clock[0],
            updated_at=clock[0],
            role=CameraRole.IDENTITY_ONLY,
        )
    )
    repository = InMemoryEntryIdentityEventRepository(clock=lambda: clock[0])
    service = EntryIdentityEventService(
        repository,
        streams,
        retention_days=7,
        page_size_max=200,
        clock=lambda: clock[0],
    )
    app.dependency_overrides[get_entry_identity_event_service] = lambda: service
    with TestClient(app) as client:
        yield client, clock
    app.dependency_overrides.pop(get_entry_identity_event_service, None)


def event_payload(
    *,
    captured_at: datetime = datetime(2026, 8, 24, 8, 59, tzinfo=UTC),
    sequence: int = 7,
    status: str = "REGISTERED",
) -> dict[str, object]:
    student_id = "student-001" if status == "REGISTERED" else None
    similarity = 0.86 if status != "UNCERTAIN" else None
    margin = 0.31 if status != "UNCERTAIN" else None
    observation = {
        "face_track_id": f"face-{sequence}",
        "face_bbox": [40, 20, 80, 65],
        "detection_confidence": 0.94,
        "identity_status": status,
        "student_id": student_id,
        "similarity": similarity,
        "margin": margin,
        "quality": 0.81,
        "observation_count": 4,
        "rejected_reason": None if status == "REGISTERED" else "open_set_threshold",
    }
    milliseconds = int(captured_at.timestamp() * 1000)
    return {
        "event_id": f"entry-camera-{milliseconds}-{sequence}-entry-face",
        "camera_id": "entry-camera",
        "captured_at": captured_at.isoformat(),
        "sequence": sequence,
        "frame": {"width_pixels": 160, "height_pixels": 120},
        "processing_status": "SUCCEEDED",
        "observations": [observation],
    }


def test_신규_201_동일_재전송_200_다른_본문_409(
    client_and_clock: tuple[TestClient, list[datetime]],
) -> None:
    client, _ = client_and_clock
    payload = event_payload()

    created = client.post("/internal/entry-identity-events", json=payload)
    duplicate = client.post("/internal/entry-identity-events", json=payload)
    changed = dict(payload)
    changed["observations"] = [
        {**payload["observations"][0], "quality": 0.7}  # type: ignore[index]
    ]
    conflict = client.post("/internal/entry-identity-events", json=changed)

    assert created.status_code == 201
    assert duplicate.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "ENTRY_IDENTITY_EVENT_CONFLICT"


@pytest.mark.parametrize("status", ["REGISTERED", "UNKNOWN", "UNCERTAIN"])
def test_모든_얼굴_판정_상태를_저장한다(
    client_and_clock: tuple[TestClient, list[datetime]],
    status: str,
) -> None:
    client, _ = client_and_clock
    payload = event_payload(
        status=status, sequence={"REGISTERED": 1, "UNKNOWN": 2, "UNCERTAIN": 3}[status]
    )

    response = client.post("/internal/entry-identity-events", json=payload)

    assert response.status_code == 201
    assert response.json()["observations"][0]["identity_status"] == status
    serialized = response.text.lower()
    for forbidden in ("embedding", "jpeg", "student_name", "student_number"):
        assert forbidden not in serialized


def test_분석_실패_처리_상태도_빈_관측으로_저장한다(
    client_and_clock: tuple[TestClient, list[datetime]],
) -> None:
    client, _ = client_and_clock
    payload = event_payload()
    payload["processing_status"] = "ANALYZER_UNAVAILABLE"
    payload["observations"] = []

    response = client.post("/internal/entry-identity-events", json=payload)

    assert response.status_code == 201
    assert response.json()["processing_status"] == "ANALYZER_UNAVAILABLE"


def test_embedding_같은_민감_추가_필드는_수신하지_않는다(
    client_and_clock: tuple[TestClient, list[datetime]],
) -> None:
    client, _ = client_and_clock
    payload = event_payload()
    payload["observations"][0]["embedding"] = [0.1]  # type: ignore[index]

    response = client.post("/internal/entry-identity-events", json=payload)

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("mutate", "expected_type"),
    [
        (lambda payload: payload.update(captured_at="2026-08-24T08:59:00"), "value_error"),
        (
            lambda payload: payload["observations"][0].update(face_bbox=[40, 20, 180, 65]),
            "value_error",
        ),
        (
            lambda payload: payload["observations"][0].update(student_id=None),
            "value_error",
        ),
    ],
)
def test_timezone_bbox_상태별_필드_조합을_검증한다(
    client_and_clock: tuple[TestClient, list[datetime]],
    mutate: Callable[[dict[str, object]], None],
    expected_type: str,
) -> None:
    client, _ = client_and_clock
    payload = event_payload()
    mutate(payload)

    response = client.post("/internal/entry-identity-events", json=payload)

    assert response.status_code == 422
    assert any(
        item["type"].startswith(expected_type)
        for item in response.json()["error"]["details"]["errors"]
    )


def test_SEAT_JUDGING_카메라의_입구_이벤트는_거부한다(
    client_and_clock: tuple[TestClient, list[datetime]],
) -> None:
    client, _ = client_and_clock
    payload = event_payload()
    payload["camera_id"] = "classroom-cctv"
    payload["event_id"] = str(payload["event_id"]).replace("entry-camera-", "classroom-cctv-", 1)

    response = client.post("/internal/entry-identity-events", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "ENTRY_IDENTITY_CAMERA_ROLE_INVALID"


def test_미등록_또는_비활성_입구_카메라는_수신하지_않는다(
    client_and_clock: tuple[TestClient, list[datetime]],
) -> None:
    client, _ = client_and_clock
    missing = event_payload()
    missing["camera_id"] = "missing-entry"
    missing["event_id"] = str(missing["event_id"]).replace("entry-camera-", "missing-entry-", 1)
    inactive = event_payload(sequence=8)
    inactive["camera_id"] = "inactive-entry-camera"
    inactive["event_id"] = str(inactive["event_id"]).replace(
        "entry-camera-", "inactive-entry-camera-", 1
    )

    missing_response = client.post("/internal/entry-identity-events", json=missing)
    inactive_response = client.post("/internal/entry-identity-events", json=inactive)

    assert missing_response.status_code == 404
    assert inactive_response.status_code == 422


def test_상태_학생_시간_cursor로_관리_조회한다(
    client_and_clock: tuple[TestClient, list[datetime]],
) -> None:
    client, _ = client_and_clock
    first = event_payload(
        captured_at=datetime(2026, 8, 24, 8, 55, tzinfo=UTC),
        sequence=1,
        status="UNKNOWN",
    )
    second = event_payload(
        captured_at=datetime(2026, 8, 24, 8, 56, tzinfo=UTC),
        sequence=2,
        status="REGISTERED",
    )
    third = event_payload(
        captured_at=datetime(2026, 8, 24, 8, 57, tzinfo=UTC),
        sequence=3,
        status="REGISTERED",
    )
    for payload in (first, second, third):
        assert client.post("/internal/entry-identity-events", json=payload).status_code == 201

    page = client.get(
        "/api/v1/video-streams/entry-stream/entry-identity-events",
        params={"status": "REGISTERED", "student_id": "student-001", "limit": 1},
    )
    cursor = page.json()["next_cursor"]
    next_page = client.get(
        "/api/v1/video-streams/entry-stream/entry-identity-events",
        params={"status": "REGISTERED", "student_id": "student-001", "limit": 1, "cursor": cursor},
    )

    assert page.status_code == 200
    assert page.json()["items"][0]["event_id"] == third["event_id"]
    assert next_page.json()["items"][0]["event_id"] == second["event_id"]

    time_page = client.get(
        "/api/v1/video-streams/entry-stream/entry-identity-events",
        params={
            "from": "2026-08-24T08:56:30+00:00",
            "to": "2026-08-24T08:57:30+00:00",
        },
    )
    assert [item["event_id"] for item in time_page.json()["items"]] == [third["event_id"]]


def test_필터가_없으면_최신_50건을_조회한다(
    client_and_clock: tuple[TestClient, list[datetime]],
) -> None:
    client, _ = client_and_clock
    for sequence in range(51):
        payload = event_payload(
            captured_at=datetime(2026, 8, 23, tzinfo=UTC) + timedelta(seconds=sequence),
            sequence=sequence,
        )
        assert client.post("/internal/entry-identity-events", json=payload).status_code == 201

    response = client.get("/api/v1/video-streams/entry-stream/entry-identity-events")

    assert response.status_code == 200
    assert len(response.json()["items"]) == 50
    assert response.json()["items"][0]["sequence"] == 50
    assert response.json()["items"][-1]["sequence"] == 1


def test_memory_저장소는_7일이_지나면_조회에서_제거한다(
    client_and_clock: tuple[TestClient, list[datetime]],
) -> None:
    client, clock = client_and_clock
    assert client.post("/internal/entry-identity-events", json=event_payload()).status_code == 201
    clock[0] += timedelta(days=7, seconds=1)

    response = client.get("/api/v1/video-streams/entry-stream/entry-identity-events")

    assert response.status_code == 200
    assert response.json()["items"] == []
