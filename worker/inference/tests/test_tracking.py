from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest
from shared.types import CapturedFrame

from ..tracking import ByteTrackConfig, ByteTrackResultHandler, CameraByteTracker
from ..types import Detection, InferenceResult


def result(*detections: Detection) -> InferenceResult:
    return InferenceResult((200, 300, 3), detections)


def person(confidence: float, bbox: tuple[int, int, int, int]) -> Detection:
    return Detection(0, "person", confidence, bbox)


def captured(camera_id: str, sequence: int) -> CapturedFrame:
    return CapturedFrame(
        camera_id=camera_id,
        frame=np.zeros((200, 300, 3), dtype=np.uint8),
        captured_at=datetime(2026, 8, 22, 9, 0, sequence, tzinfo=UTC),
        sequence=sequence,
    )


def config(**overrides: object) -> ByteTrackConfig:
    values: dict[str, object] = {
        "high_confidence_threshold": 0.5,
        "low_confidence_threshold": 0.1,
        "new_track_threshold": 0.6,
        "first_match_iou_threshold": 0.3,
        "second_match_iou_threshold": 0.2,
        "track_buffer_frames": 2,
    }
    values.update(overrides)
    return ByteTrackConfig(**values)  # type: ignore[arg-type]


def test_연속된_사람_bbox에_같은_track_id를_붙인다() -> None:
    tracker = CameraByteTracker(config())

    first = tracker.update(result(person(0.9, (10, 10, 80, 180))))
    second = tracker.update(result(person(0.85, (14, 10, 84, 180))))

    assert first.detections[0].track_id == "person-1"
    assert second.detections[0].track_id == "person-1"


def test_실제_촬영_간격으로_빠르게_이동한_bbox를_예측한다() -> None:
    tracker = CameraByteTracker(config())

    first = tracker.update(result(person(0.9, (0, 10, 20, 180))), observed_at=0.0)
    tracker.update(result(person(0.9, (10, 10, 30, 180))), observed_at=0.2)
    third = tracker.update(result(person(0.9, (50, 10, 70, 180))), observed_at=1.0)

    assert third.detections[0].track_id == first.detections[0].track_id


def test_예측으로_설명되지_않는_큰_단절은_새_track으로_둔다() -> None:
    tracker = CameraByteTracker(config())

    first = tracker.update(result(person(0.9, (0, 10, 20, 180))), observed_at=0.0)
    tracker.update(result(person(0.9, (10, 10, 30, 180))), observed_at=0.2)
    third = tracker.update(result(person(0.9, (180, 10, 200, 180))), observed_at=1.0)

    assert third.detections[0].track_id != first.detections[0].track_id


def test_낮은_신뢰도_탐지도_2단계에서_기존_track을_유지한다() -> None:
    tracker = CameraByteTracker(config())
    tracker.update(result(person(0.9, (10, 10, 80, 180))))

    low_confidence = tracker.update(result(person(0.3, (13, 10, 83, 180))))

    assert low_confidence.detections[0].track_id == "person-1"
    assert tracker.created_last_update == 0


def test_낮은_신뢰도_탐지만으로_새_track을_만들지_않는다() -> None:
    tracker = CameraByteTracker(config())

    tracked = tracker.update(result(person(0.3, (10, 10, 80, 180))))

    assert tracked.detections[0].track_id is None
    assert tracker.active_track_count == 0


def test_짧은_미탐_뒤에도_이동_예측으로_track을_회복한다() -> None:
    tracker = CameraByteTracker(config(track_buffer_frames=3))
    tracker.update(result(person(0.9, (10, 10, 60, 180))))
    tracker.update(result(person(0.9, (20, 10, 70, 180))))
    tracker.update(result())

    recovered = tracker.update(result(person(0.9, (40, 10, 90, 180))))

    assert recovered.detections[0].track_id == "person-1"


def test_buffer를_넘긴_track은_만료하고_새_id를_만든다() -> None:
    tracker = CameraByteTracker(config(track_buffer_frames=1))
    tracker.update(result(person(0.9, (10, 10, 80, 180))))
    tracker.update(result())
    tracker.update(result())
    assert tracker.expired_track_ids_last_update == ("person-1",)

    recreated = tracker.update(result(person(0.9, (10, 10, 80, 180))))

    assert recreated.detections[0].track_id == "person-2"
    assert tracker.expired_last_update == 0  # 직전 빈 프레임에서 이미 만료됨


def test_휴대폰_detection에는_track_id를_붙이지_않는다() -> None:
    tracker = CameraByteTracker(config())
    phone = Detection(67, "cell phone", 0.9, (20, 20, 40, 50))

    tracked = tracker.update(result(phone, person(0.9, (10, 10, 80, 180))))

    assert tracked.detections[0].track_id is None
    assert tracked.detections[1].track_id == "person-1"


def test_카메라마다_track_id_상태를_분리한다() -> None:
    handled: list[tuple[str, InferenceResult]] = []
    handler = ByteTrackResultHandler(
        config(), inner=lambda frame, value: handled.append((frame.camera_id, value))
    )

    handler(captured("entry-camera", 0), result(person(0.9, (10, 10, 80, 180))))
    handler(captured("classroom-cctv", 0), result(person(0.9, (150, 10, 220, 180))))

    assert handled[0][1].detections[0].track_id == "person-1"
    assert handled[1][1].detections[0].track_id == "person-1"
    assert set(handler._trackers) == {"entry-camera", "classroom-cctv"}


def test_만료한_track_ID를_인계_상태_정리기로_전달한다() -> None:
    expired: list[tuple[str, tuple[str, ...]]] = []
    handler = ByteTrackResultHandler(
        config(track_buffer_frames=1),
        inner=lambda _frame, _value: None,
        expired_track_handler=lambda camera_id, track_ids: expired.append(
            (camera_id, track_ids)
        ),
    )

    handler(captured("classroom-cctv", 0), result(person(0.9, (10, 10, 80, 180))))
    handler(captured("classroom-cctv", 1), result())
    handler(captured("classroom-cctv", 2), result())

    assert expired == [("classroom-cctv", ("person-1",))]


def test_잘못된_threshold_조합은_거부한다() -> None:
    with pytest.raises(ValueError, match="낮은 신뢰도"):
        ByteTrackConfig(low_confidence_threshold=0.7, high_confidence_threshold=0.5)
