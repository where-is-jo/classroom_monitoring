"""입구 얼굴 식별부터 교실 CCTV track 유지까지 실제 핸들러 체인을 검증한다."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
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


def registered_entry_batch() -> EntryFaceObservationBatch:
    return EntryFaceObservationBatch(
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
    )


def recovery_pipeline() -> tuple[
    ByteTrackResultHandler,
    list[tuple[CapturedFrame, InferenceResult]],
]:
    handled: list[tuple[CapturedFrame, InferenceResult]] = []
    handover = IdentityHandoverResultHandler(
        (
            IdentityHandoverRoute(
                "entry-camera",
                "classroom-cctv",
                (0.0, 0.0, 1.0, 1.0),
            ),
        ),
        inner=lambda frame, value: handled.append((frame, value)),
        identity_track_recovery_enabled=True,
    )
    pipeline = ByteTrackResultHandler(
        ByteTrackConfig(
            kalman_enabled=True,
            track_lifecycle_enabled=True,
            track_buffer_frames=30,
        ),
        camera_ids=frozenset({"classroom-cctv"}),
        inner=handover,
        internal_track_handler=handover.observe_classroom_tracking,
        transition_handler=handover.handle_track_transitions,
        expired_track_handler=handover.expire_classroom_tracks,
    )
    handover.observe_entry(
        captured("entry-camera", seconds=0, sequence=0),
        registered_entry_batch(),
    )
    return pipeline, handled


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
        registered_entry_batch(),
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


def test_tentative_문영역_진입을_내부에서_관측해_신원을_붙인다() -> None:
    handled: list[tuple[CapturedFrame, InferenceResult]] = []
    handover = IdentityHandoverResultHandler(
        (
            IdentityHandoverRoute(
                "entry-camera",
                "classroom-cctv",
                (0.0, 0.0, 0.25, 1.0),
            ),
        ),
        inner=lambda frame, value: handled.append((frame, value)),
    )
    pipeline = ByteTrackResultHandler(
        ByteTrackConfig(kalman_enabled=True, track_lifecycle_enabled=True),
        camera_ids=frozenset({"classroom-cctv"}),
        inner=handover,
        internal_track_handler=handover.observe_classroom_tracking,
        expired_track_handler=handover.expire_classroom_tracks,
    )
    handover.observe_entry(
        captured("entry-camera", seconds=0, sequence=1),
        registered_entry_batch(),
    )

    # 첫 tentative는 문 영역 안에 있고 외부에서는 숨긴다. 두 번째 관측은 이미
    # 문 영역 밖이지만, 첫 관측의 내부 bookkeeping 덕분에 신원을 잃지 않는다.
    pipeline(
        captured("classroom-cctv", seconds=1, sequence=1),
        person((0, 0, 30, 90)),
    )
    pipeline(
        captured("classroom-cctv", seconds=2, sequence=2),
        person((15, 0, 45, 90)),
    )

    assert handled[0][1].detections == ()
    promoted = handled[1][1].detections[0]
    assert promoted.track_id == "person-1"
    assert promoted.student_id == "student-001"


@pytest.mark.parametrize("missing_frames", (1, 2, 3, 4, 5))
def test_일반_가림_5회에서_같은_track과_신원을_복구한다(
    missing_frames: int,
) -> None:
    pipeline, handled = recovery_pipeline()
    bbox = (40, 0, 50, 90)
    pipeline(
        captured("classroom-cctv", seconds=1.0, sequence=1),
        person(bbox),
    )
    pipeline(
        captured("classroom-cctv", seconds=1.2, sequence=2),
        person(bbox),
    )
    for missing in range(missing_frames):
        pipeline(
            captured(
                "classroom-cctv",
                seconds=1.4 + missing * 0.2,
                sequence=3 + missing,
            ),
            InferenceResult((100, 100, 3), ()),
        )

    pipeline(
        captured(
            "classroom-cctv",
            seconds=1.4 + missing_frames * 0.2,
            sequence=3 + missing_frames,
        ),
        person(bbox),
    )

    recovered = handled[-1][1].detections[0]
    assert recovered.track_id == "person-1"
    assert recovered.student_id == "student-001"


@pytest.mark.parametrize("offset", (0, 1, 2, 3, 4))
def test_강제_IoU_단절_5회에서_새_track으로_신원을_이동한다(
    offset: int,
) -> None:
    pipeline, handled = recovery_pipeline()
    old_bbox = (40 + offset, 0, 50 + offset, 90)
    new_bbox = (51 + offset, 0, 61 + offset, 90)
    pipeline(
        captured("classroom-cctv", seconds=1.0, sequence=1),
        person(old_bbox),
    )
    pipeline(
        captured("classroom-cctv", seconds=1.2, sequence=2),
        person(old_bbox),
    )
    assert handled[-1][1].detections[0].student_id == "student-001"

    # IoU는 0이지만 발 중심 거리는 프레임 대각선의 0.08 이하다.
    pipeline(
        captured("classroom-cctv", seconds=1.4, sequence=3),
        person(new_bbox),
    )
    pipeline(
        captured("classroom-cctv", seconds=1.6, sequence=4),
        person(new_bbox),
    )
    recovered = handled[-1][1].detections[0]
    assert recovered.track_id == "person-2"
    assert recovered.student_id == "student-001"

    # 옛 lost track이 재획득돼도 이동한 신원은 되살아나지 않는다.
    pipeline(
        captured("classroom-cctv", seconds=1.8, sequence=5),
        person(old_bbox),
    )
    resurrected = handled[-1][1].detections[0]
    assert resurrected.track_id == "person-1"
    assert resurrected.student_id is None
