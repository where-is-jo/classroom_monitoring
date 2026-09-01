from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

import numpy as np

from shared.types import CapturedFrame

from ..handler import build_event_payload
from ..identity_handover import IdentityHandoverResultHandler, IdentityHandoverRoute
from ..tracking import ByteTrackConfig, ByteTrackResultHandler
from ..types import (
    Detection,
    EntryFaceObservation,
    EntryFaceObservationBatch,
    EntryIdentityProcessingStatus,
    EntryIdentityStatus,
    InferenceResult,
)

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
    handled: list[tuple[CapturedFrame, InferenceResult]] = []
    handover = IdentityHandoverResultHandler(
        (
            IdentityHandoverRoute(
                "entry-camera",
                "classroom-cctv",
                (0.45, 0.0, 0.65, 1.0),
            ),
        ),
        inner=lambda frame, result: handled.append((frame, result)),
        maximum_delay_seconds=8,
        clock_skew_seconds=0.5,
        track_stale_seconds=30,
        minimum_identity_confidence=0.6,
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
        camera_ids=frozenset({"classroom-cctv"}),
        inner=handover,
    )

    handover.observe_entry(
        _captured("entry-camera", 1, 0),
        EntryFaceObservationBatch(
            frame_shape=(100, 200, 3),
            processing_status=EntryIdentityProcessingStatus.SUCCEEDED,
            observations=(
                EntryFaceObservation(
                    face_track_id="face-1",
                    face_bbox=(70, 10, 110, 50),
                    detection_confidence=0.96,
                    identity_status=EntryIdentityStatus.REGISTERED,
                    student_id="student-001",
                    similarity=0.93,
                    margin=0.3,
                    quality=0.85,
                    observation_count=4,
                    rejected_reason=None,
                ),
            ),
        ),
    )
    # CCTV track은 인계 ROI 밖에서 먼저 만들어진다. 실제 카메라에서 이미 보이던
    # person-N이 문 영역으로 걸어 들어오는 경우를 재현한다.
    pipeline(
        _captured("classroom-cctv", 2, 0),
        _raw_person((130, 5, 180, 95)),
    )
    for sequence, left in enumerate((110,), start=1):
        pipeline(
            _captured("classroom-cctv", 2 + sequence, sequence),
            _raw_person((left, 5, left + 50, 95)),
        )
    before_entry = handled[-1][1].detections[0]
    assert before_entry.track_id == "person-1"
    assert before_entry.student_id is None

    # bbox 발 중심이 ROI 안으로 교차한 순간 같은 ByteTrack에 입구 신원이 잠긴다.
    for sequence, left in enumerate((90, 70, 50, 30, 10), start=2):
        pipeline(
            _captured("classroom-cctv", 2 + sequence, sequence),
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
