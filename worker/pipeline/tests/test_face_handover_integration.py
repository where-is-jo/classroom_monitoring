"""입구 얼굴 식별부터 교실 CCTV track 유지까지 실제 핸들러 체인을 검증한다."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
from inference.identity_handover import (
    IdentityHandoverResultHandler,
    IdentityHandoverRoute,
)
from inference.tracking import ByteTrackConfig, ByteTrackResultHandler
from inference.types import (
    Detection,
    EntryFaceObservation,
    EntryFaceObservationBatch,
    EntryIdentityProcessingStatus,
    EntryIdentityStatus,
    InferenceResult,
)
from shared.types import CapturedFrame


BASE_TIME = datetime(2026, 8, 24, tzinfo=UTC)


def captured(camera_id: str, *, seconds: float, sequence: int) -> CapturedFrame:
    return CapturedFrame(
        camera_id=camera_id,
        frame=np.zeros((100, 100, 3), dtype=np.uint8),
        captured_at=BASE_TIME + timedelta(seconds=seconds),
        sequence=sequence,
    )


def person(bbox: tuple[int, int, int, int]) -> InferenceResult:
    return InferenceResult(
        frame_shape=(100, 100, 3),
        detections=(Detection(0, "person", 0.9, bbox),),
    )


def test_입구에서_확정한_학생이_CCTV_track을_따라_좌석_방향까지_유지된다() -> None:
    handled: list[tuple[CapturedFrame, InferenceResult]] = []
    handover = IdentityHandoverResultHandler(
        (
            IdentityHandoverRoute(
                "entry-camera",
                "classroom-cctv",
                (0.0, 0.0, 0.25, 1.0),
            ),
        ),
        inner=lambda frame, result: handled.append((frame, result)),
    )

    pipeline = ByteTrackResultHandler(
        ByteTrackConfig(),
        camera_ids=frozenset({"classroom-cctv"}),
        inner=handover,
    )

    handover.observe_entry(
        captured("entry-camera", seconds=0, sequence=1),
        EntryFaceObservationBatch(
            frame_shape=(100, 100, 3),
            processing_status=EntryIdentityProcessingStatus.SUCCEEDED,
            observations=(
                EntryFaceObservation(
                    face_track_id="face-1",
                    face_bbox=(10, 10, 30, 30),
                    detection_confidence=0.95,
                    identity_status=EntryIdentityStatus.REGISTERED,
                    student_id="student-001",
                    similarity=0.57,
                    margin=0.2,
                    quality=0.8,
                    observation_count=4,
                    rejected_reason=None,
                ),
            ),
        ),
    )
    pipeline(
        captured("classroom-cctv", seconds=1, sequence=1),
        person((0, 0, 40, 90)),
    )
    pipeline(
        captured("classroom-cctv", seconds=2, sequence=2),
        person((10, 0, 50, 90)),
    )

    doorway = handled[0][1].detections[0]
    toward_seat = handled[1][1].detections[0]
    assert doorway.track_id is not None
    assert doorway.student_id == "student-001"
    assert doorway.identity_confidence == 0.57
    assert toward_seat.track_id == doorway.track_id
    assert toward_seat.student_id == "student-001"
