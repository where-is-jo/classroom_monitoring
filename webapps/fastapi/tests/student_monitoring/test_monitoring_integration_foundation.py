"""모델 없이 worker 이벤트부터 학생 상태까지 잇는 합성 통합 검증."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from pymongo import DESCENDING
from pymongo.errors import DuplicateKeyError

from app.classrooms.adapters.memory_repository import (
    InMemoryClassroomRepository,
    InMemorySeatAssignmentRepository,
)
from app.classrooms.models import CreateClassroomCommand, CreateSeatCommand, SeatGeometry
from app.classrooms.service import ClassroomService
from app.main import app
from app.roi_connections.adapters.memory import InMemoryRoiConnectionRepository
from app.roi_connections.models import Point, RoiConnection
from app.roi_connections.service import RoiConnectionService
from app.shared.adapters.memory_student_lookup import InMemoryStudentLookup
from app.shared.broadcaster import InMemoryBroadcaster
from app.shared.database import MongoDatabase, MongoDocument
from app.shared.dependencies import get_student_monitoring_service
from app.shared.student_identity import StudentIdentity
from app.student_monitoring.adapters.memory_repository import (
    MemoryDetectionEventRepository,
    MemoryVideoSegmentRepository,
)
from app.student_monitoring.adapters.mongo_repository import MongoDetectionEventRepository
from app.student_monitoring.models import StudentState
from app.student_monitoring.ports import DetectionEventRepository
from app.student_monitoring.schemas import InferenceEventRequest
from app.student_monitoring.service import StudentMonitoringService
from app.video_monitoring.adapters.memory_repository import MemoryVideoStreamRepository
from app.video_monitoring.models import PlaybackKind, VideoStream

CLASSROOM_ID = "synthetic-classroom-001"
CAMERA_ID = "synthetic-camera-001"
STUDENT_ID = "synthetic-student-001"
NOW = datetime(2026, 8, 15, 3, 1, tzinfo=UTC)
FIXTURE_PATH = (
    Path(__file__).resolve().parents[4]
    / "worker"
    / "inference"
    / "fixtures"
    / "identified_student_event.json"
)


@dataclass(frozen=True)
class IntegrationContext:
    service: StudentMonitoringService
    detection_repository: DetectionEventRepository
    broadcaster: InMemoryBroadcaster


class PersistentMongoCursor:
    def __init__(self, documents: list[MongoDocument]) -> None:
        self._documents = documents
        self._limit: int | None = None

    def sort(self, fields: list[tuple[str, int]]) -> PersistentMongoCursor:
        for field, direction in reversed(fields):
            self._documents.sort(
                key=lambda document: document[field],
                reverse=direction == DESCENDING,
            )
        return self

    def limit(self, value: int) -> PersistentMongoCursor:
        self._limit = value
        return self

    def __iter__(self) -> Iterator[MongoDocument]:
        return iter(self._documents[: self._limit])


class PersistentMongoCollection:
    """프로세스 재조립 뒤에도 같은 문서를 돌려주는 Mongo collection 대역."""

    def __init__(self) -> None:
        self.documents: dict[str, MongoDocument] = {}

    def insert_one(self, document: MongoDocument) -> None:
        event_id = str(document["_id"])
        if event_id in self.documents:
            raise DuplicateKeyError("duplicate synthetic event")
        self.documents[event_id] = deepcopy(document)

    def find_one(self, query: MongoDocument) -> MongoDocument | None:
        document = self.documents.get(str(query.get("_id", "")))
        return deepcopy(document) if document is not None else None

    def find(self, query: MongoDocument) -> PersistentMongoCursor:
        documents = [
            deepcopy(document)
            for document in self.documents.values()
            if self._matches(document, query)
        ]
        return PersistentMongoCursor(documents)

    @staticmethod
    def _matches(document: MongoDocument, query: MongoDocument) -> bool:
        for field, expected in query.items():
            actual = document.get(field)
            if isinstance(expected, dict) and "$gte" in expected:
                if actual is None or actual < expected["$gte"]:
                    return False
            elif actual != expected:
                return False
        return True


class PersistentMongoDatabase:
    def __init__(self, collection: PersistentMongoCollection) -> None:
        self.collection = collection

    def __getitem__(self, name: str) -> PersistentMongoCollection:
        assert name == "detection_events"
        return self.collection


def _fixture_payload() -> dict[str, Any]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_candidate_worker_fixture_matches_fastapi_request_contract() -> None:
    candidate_path = Path(os.environ.get("INFERENCE_EVENT_FIXTURE", FIXTURE_PATH))
    payload = json.loads(candidate_path.read_text(encoding="utf-8"))

    validated = InferenceEventRequest.model_validate(payload)

    assert validated.event_id
    assert validated.camera_id


def _rectangle(left: float, top: float, right: float, bottom: float) -> tuple[Point, ...]:
    return (
        Point(left, top),
        Point(right, top),
        Point(right, bottom),
        Point(left, bottom),
    )


def _build_context(
    detection_repository: DetectionEventRepository,
    *,
    overlapping_rois: bool = False,
) -> IntegrationContext:
    students = InMemoryStudentLookup(
        (
            StudentIdentity(STUDENT_ID, "SYNTHETIC-001", "합성 학생 A", True),
            StudentIdentity("synthetic-student-002", "SYNTHETIC-002", "합성 학생 B", True),
        )
    )
    classroom_repository = InMemoryClassroomRepository()
    assignment_repository = InMemorySeatAssignmentRepository(classroom_repository)
    classroom_service = ClassroomService(
        classroom_repository,
        student_lookup=students,
        assignment_repository=assignment_repository,
        occupancy_confidence_threshold=0.6,
        clock=lambda: NOW,
    )
    classroom_service.seed_classroom(
        CreateClassroomCommand(CLASSROOM_ID, "SYN-001", "합성 강의실", "테스트")
    )
    for seat_id, code, x in (
        ("synthetic-seat-001", "S01", 0.1),
        ("synthetic-seat-002", "S02", 0.5),
    ):
        classroom_service.seed_seat(
            CreateSeatCommand(
                id=seat_id,
                classroom_id=CLASSROOM_ID,
                code=code,
                label=f"좌석 {code}",
                geometry=SeatGeometry(x=x, y=0.1, width=0.2, height=0.6),
            )
        )
    classroom_service.assign_student("synthetic-seat-001", STUDENT_ID)
    classroom_service.assign_student("synthetic-seat-002", "synthetic-student-002")

    streams = MemoryVideoStreamRepository()
    streams.save(
        VideoStream(
            id="synthetic-stream-001",
            camera_id=CAMERA_ID,
            classroom_id=CLASSROOM_ID,
            camera_label="합성 카메라",
            playback_kind=PlaybackKind.WEBRTC,
            playback_path="/webrtc/synthetic-camera-001",
            enabled=True,
            last_frame_at=None,
            last_detection_at=None,
            is_demo=False,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    roi_repository = InMemoryRoiConnectionRepository()
    seat_one_polygon = _rectangle(0.1, 0.1, 0.3, 0.7)
    roi_repository.save(
        RoiConnection(
            classroom_id=CLASSROOM_ID,
            camera_id=CAMERA_ID,
            seat_id="synthetic-seat-001",
            student_id="synthetic-student-002",
            polygon=seat_one_polygon,
            reference_image_revision=0,
            updated_at=NOW,
        )
    )
    roi_repository.save(
        RoiConnection(
            classroom_id=CLASSROOM_ID,
            camera_id=CAMERA_ID,
            seat_id="synthetic-seat-002",
            student_id=STUDENT_ID,
            polygon=(seat_one_polygon if overlapping_rois else _rectangle(0.5, 0.1, 0.7, 0.7)),
            reference_image_revision=0,
            updated_at=NOW,
        )
    )
    roi_service = RoiConnectionService(
        classroom_service,
        students,
        roi_repository,
        streams,
        max_upload_bytes=1024,
        page_size_max=200,
        clock=lambda: NOW,
    )
    broadcaster = InMemoryBroadcaster()
    return IntegrationContext(
        service=StudentMonitoringService(
            detection_repository=detection_repository,
            segment_repository=MemoryVideoSegmentRepository(),
            stream_repository=streams,
            broadcaster=broadcaster,
            classroom_service=classroom_service,
            roi_service=roi_service,
            occupancy_confidence_threshold=0.6,
            identity_confidence_threshold=0.7,
            stale_seconds=300,
            recent_event_limit=500,
            clock=lambda: NOW,
            student_lookup=students,
        ),
        detection_repository=detection_repository,
        broadcaster=broadcaster,
    )


def _post_and_get_states(
    context: IntegrationContext, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    app.dependency_overrides[get_student_monitoring_service] = lambda: context.service
    try:
        client = TestClient(app)
        post_response = client.post("/internal/inference/events", json=payload)
        states_response = client.get(f"/api/v1/classrooms/{CLASSROOM_ID}/student-states")
    finally:
        app.dependency_overrides.clear()
    assert states_response.status_code == 200
    return post_response.status_code, cast(dict[str, Any], states_response.json())


def _drain_events(context: IntegrationContext, queue: Any) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    while not queue.empty():
        event = queue.get_nowait()
        assert isinstance(event, dict)
        events.append(event)
    return events


def test_approved_fixture_runs_post_rest_and_all_sse_payloads_idempotently() -> None:
    context = _build_context(MemoryDetectionEventRepository())
    queue = context.broadcaster.subscribe()
    payload = _fixture_payload()

    first_status, first_states = _post_and_get_states(context, payload)
    second_status, second_states = _post_and_get_states(context, payload)
    events = _drain_events(context, queue)

    assert InferenceEventRequest.model_validate(payload)
    assert first_status == 201
    assert second_status == 200
    assert first_states == second_states
    saved = context.detection_repository.find_by_event_id(str(payload["event_id"]))
    assert saved is not None
    assert saved.stream_id == "synthetic-stream-001"
    assert saved.classroom_id == CLASSROOM_ID
    assert saved.detections[0].student_id == STUDENT_ID
    assert len(context.detection_repository.find_recent_by_camera(CAMERA_ID, 10)) == 1
    assert [state["current_state"] for state in first_states["states"]] == [
        "PRESENT",
        "UNKNOWN",
    ]

    detection_events = [event for event in events if event["type"] == "detection"]
    occupancy_events = [event for event in events if event["type"] == "occupancy"]
    student_events = [event for event in events if event["type"] == "student-state"]
    assert len(detection_events) == 1
    assert len(occupancy_events) == 2
    assert len(student_events) == 1
    assert detection_events[0]["detections"][0]["display_label"] == "합성 학생 A"
    assert "identity_confidence" not in detection_events[0]["detections"][0]
    assert student_events[0]["current_state"] == "PRESENT"


def test_same_student_in_other_roi_becomes_wrong_seat() -> None:
    context = _build_context(MemoryDetectionEventRepository())
    payload = _fixture_payload()
    wrong_seat_payload = deepcopy(payload)
    wrong_seat_payload["event_id"] = "synthetic-camera-001-20260815T030010000Z-43"
    wrong_seat_payload["captured_at"] = "2026-08-15T03:00:10+00:00"
    wrong_seat_payload["sequence"] = 43
    wrong_seat_payload["detections"][0]["detection_id"] = (
        "synthetic-camera-001-20260815T030010000Z-43-det-0"
    )
    wrong_seat_payload["detections"][0]["bbox"] = [500, 100, 700, 700]

    status, states = _post_and_get_states(context, wrong_seat_payload)

    assert status == 201
    assert states["states"][0]["current_state"] == "WRONG_SEAT"


@pytest.mark.parametrize(
    ("case", "overlap"),
    (
        ("unidentified", False),
        ("low-detection", False),
        ("low-identity", False),
        ("outside-roi", False),
        ("overlapping-roi", True),
    ),
)
def test_insufficient_or_ambiguous_evidence_stays_unknown(case: str, overlap: bool) -> None:
    context = _build_context(MemoryDetectionEventRepository(), overlapping_rois=overlap)
    payload = _fixture_payload()
    detection = payload["detections"][0]
    if case == "unidentified":
        detection.pop("student_id")
        detection.pop("identity_confidence")
        detection.pop("face_bbox")
    elif case == "low-detection":
        detection["confidence"] = 0.59
    elif case == "low-identity":
        detection["identity_confidence"] = 0.69
    elif case == "outside-roi":
        detection["bbox"] = [800, 800, 900, 900]

    status, states = _post_and_get_states(context, payload)

    assert status == 201
    assert states["states"][0]["current_state"] == "UNKNOWN"


def test_new_service_restores_latest_state_from_persisted_mongo_event() -> None:
    collection = PersistentMongoCollection()
    database = cast(MongoDatabase, PersistentMongoDatabase(collection))
    first_context = _build_context(MongoDetectionEventRepository(database))

    first_status, first_states = _post_and_get_states(first_context, _fixture_payload())
    restarted_context = _build_context(MongoDetectionEventRepository(database))
    app.dependency_overrides[get_student_monitoring_service] = lambda: restarted_context.service
    try:
        restored_response = TestClient(app).get(f"/api/v1/classrooms/{CLASSROOM_ID}/student-states")
    finally:
        app.dependency_overrides.clear()

    assert first_status == 201
    assert first_states["states"][0]["current_state"] == StudentState.PRESENT.value
    assert restored_response.status_code == 200
    assert restored_response.json()["states"][0]["current_state"] == StudentState.PRESENT.value
    assert list(collection.documents) == ["synthetic-camera-001-20260815T030000000Z-42"]
