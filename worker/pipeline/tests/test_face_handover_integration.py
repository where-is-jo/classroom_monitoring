"""입구 얼굴 식별부터 교실 CCTV track 유지까지 실제 핸들러 체인을 검증한다."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import numpy as np
from inference.face_identity import FaceIdentityResultHandler
from inference.identity_handover import (
    IdentityHandoverResultHandler,
    IdentityHandoverRoute,
)
from inference.tracking import ByteTrackConfig, ByteTrackResultHandler
from inference.types import Detection, InferenceResult
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

    class ConfirmedFaceIdentifier:
        def enrich(
            self, frame: CapturedFrame, result: InferenceResult
        ) -> InferenceResult:
            assert frame.camera_id == "entry-camera"
            identified = replace(
                result.detections[0],
                student_id="student-001",
                identity_confidence=0.57,
                face_bbox=(10, 10, 30, 30),
            )
            return InferenceResult(result.frame_shape, (identified,))

    face_identity = FaceIdentityResultHandler(
        ConfirmedFaceIdentifier(),  # type: ignore[arg-type]
        camera_ids=frozenset({"entry-camera"}),
        inner=handover,
    )
    pipeline = ByteTrackResultHandler(
        ByteTrackConfig(),
        camera_ids=frozenset({"entry-camera", "classroom-cctv"}),
        inner=face_identity,
    )

    pipeline(
        captured("entry-camera", seconds=0, sequence=1),
        person((0, 0, 40, 90)),
    )
    pipeline(
        captured("classroom-cctv", seconds=1, sequence=1),
        person((0, 0, 40, 90)),
    )
    pipeline(
        captured("classroom-cctv", seconds=2, sequence=2),
        person((10, 0, 50, 90)),
    )

    entry = handled[0][1].detections[0]
    doorway = handled[1][1].detections[0]
    toward_seat = handled[2][1].detections[0]
    assert entry.track_id is not None
    assert doorway.track_id is not None
    assert doorway.student_id == "student-001"
    assert doorway.identity_confidence == 0.57
    assert toward_seat.track_id == doorway.track_id
    assert toward_seat.student_id == "student-001"
