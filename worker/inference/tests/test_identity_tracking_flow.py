from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast

import numpy as np
from shared.types import CapturedFrame

from ..face_identity import FaceIdentityResultHandler
from ..handler import build_event_payload
from ..identity_handover import IdentityHandoverResultHandler, IdentityHandoverRoute
from ..tracking import ByteTrackConfig, ByteTrackResultHandler
from ..types import Detection, InferenceResult

STARTED_AT = datetime(2026, 8, 22, 9, 0, tzinfo=UTC)


def _captured(camera_id: str, seconds: float, sequence: int) -> CapturedFrame:
    return CapturedFrame(
        camera_id=camera_id,
        frame=np.zeros((100, 200, 3), dtype=np.uint8),
        captured_at=STARTED_AT + timedelta(seconds=seconds),
        sequence=sequence,
    )


def _raw_person(bbox: tuple[int, int, int, int]) -> InferenceResult:
    return InferenceResult(
        (100, 200, 3),
        (Detection(0, "person", 0.9, bbox),),
    )


def test_입구_얼굴_신원이_CCTV_ByteTrack을_따라_좌석_이벤트까지_간다() -> None:
    class FakeEntryIdentifier:
        def enrich(
            self, captured: CapturedFrame, result: InferenceResult
        ) -> InferenceResult:
            del captured
            person = result.detections[0]
            return InferenceResult(
                result.frame_shape,
                (
                    replace(
                        person,
                        student_id="student-001",
                        identity_confidence=0.93,
                        face_bbox=(70, 10, 110, 50),
                    ),
                ),
            )

    handled: list[tuple[CapturedFrame, InferenceResult]] = []
    handover = IdentityHandoverResultHandler(
        (
            IdentityHandoverRoute(
                "entry-camera",
                "classroom-cctv",
                (0.0, 0.0, 0.3, 1.0),
            ),
        ),
        inner=lambda frame, result: handled.append((frame, result)),
        maximum_delay_seconds=8,
        clock_skew_seconds=0.5,
        track_stale_seconds=30,
        minimum_identity_confidence=0.6,
    )
    face_identity = FaceIdentityResultHandler(
        FakeEntryIdentifier(),  # type: ignore[arg-type]
        camera_ids=frozenset({"entry-camera"}),
        inner=handover,
    )
    pipeline = ByteTrackResultHandler(
        ByteTrackConfig(
            high_confidence_threshold=0.5,
            low_confidence_threshold=0.1,
            new_track_threshold=0.6,
            first_match_iou_threshold=0.3,
            second_match_iou_threshold=0.2,
            track_buffer_frames=5,
        ),
        camera_ids=frozenset({"entry-camera", "classroom-cctv"}),
        inner=face_identity,
    )

    pipeline(
        _captured("entry-camera", 1, 0),
        _raw_person((50, 2, 150, 98)),
    )
    pipeline(
        _captured("classroom-cctv", 3, 0),
        _raw_person((0, 5, 50, 95)),
    )
    for sequence, left in enumerate((20, 40, 60, 80, 100, 120), start=1):
        pipeline(
            _captured("classroom-cctv", 3 + sequence, sequence),
            _raw_person((left, 5, left + 50, 95)),
        )

    seated_frame, seated_result = handled[-1]
    seated = seated_result.detections[0]
    assert seated_frame.camera_id == "classroom-cctv"
    assert seated.track_id == "person-1"
    assert seated.student_id == "student-001"
    assert seated.identity_confidence == 0.93
    assert seated.face_bbox is None

    payload = build_event_payload(seated_frame, seated_result)
    event_detection = cast(list[dict[str, object]], payload["detections"])[0]
    assert event_detection["track_id"] == "person-1"
    assert event_detection["student_id"] == "student-001"
    assert event_detection["identity_confidence"] == 0.93
    assert "face_bbox" not in event_detection
