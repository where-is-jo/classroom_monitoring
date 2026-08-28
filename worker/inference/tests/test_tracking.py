from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from shared.types import CapturedFrame

from ..tracking import (
    ByteTrackConfig,
    ByteTrackResultHandler,
    CameraByteTracker,
    _KalmanBBoxFilter,
)
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


def test_Kalman은_8차원_상태와_covariance를_초기화한다() -> None:
    kalman_filter = _KalmanBBoxFilter.initiate(
        np.asarray((0, 10, 20, 180), dtype=np.float64)
    )

    assert kalman_filter.mean.shape == (8,)
    assert kalman_filter.covariance.shape == (8, 8)
    assert tuple(kalman_filter.mean[:4]) == pytest.approx((10, 95, 2 / 17, 170))
    assert tuple(kalman_filter.mean[4:]) == pytest.approx((0, 0, 0, 0))


def test_Kalman_예측으로_빠르게_이동한_bbox의_ID를_유지한다() -> None:
    tracker = CameraByteTracker(config(kalman_enabled=True))

    first = tracker.update(result(person(0.9, (0, 10, 20, 180))), observed_at=0.0)
    tracker.update(result(person(0.9, (10, 10, 30, 180))), observed_at=0.2)
    third = tracker.update(result(person(0.9, (50, 10, 70, 180))), observed_at=1.0)

    assert third.detections[0].track_id == first.detections[0].track_id


def test_Kalman은_역행_timestamp에서_상태와_covariance를_이동시키지_않는다() -> None:
    kalman_filter = _KalmanBBoxFilter.initiate(
        np.asarray((0, 10, 20, 180), dtype=np.float64)
    )
    kalman_filter.mean[4] = 30.0
    previous_mean = kalman_filter.mean.copy()
    previous_covariance = kalman_filter.covariance.copy()

    kalman_filter.predict(-0.5)

    np.testing.assert_array_equal(kalman_filter.mean, previous_mean)
    np.testing.assert_array_equal(kalman_filter.covariance, previous_covariance)


def test_Kalman은_과도한_timestamp_간격을_1초로_제한한다() -> None:
    bbox = np.asarray((0, 10, 20, 180), dtype=np.float64)
    one_second = _KalmanBBoxFilter.initiate(bbox)
    long_gap = _KalmanBBoxFilter.initiate(bbox)
    one_second.mean[4] = long_gap.mean[4] = 30.0

    one_second.predict(1.0)
    long_gap.predict(30.0)

    np.testing.assert_allclose(long_gap.mean, one_second.mean)
    np.testing.assert_allclose(long_gap.covariance, one_second.covariance)


def test_Kalman을_끄면_기존_속도_예측기를_유지한다() -> None:
    tracker = CameraByteTracker(config(kalman_enabled=False))

    tracker.update(result(person(0.9, (0, 10, 20, 180))), observed_at=0.0)
    tracker.update(result(person(0.9, (10, 10, 30, 180))), observed_at=0.2)

    track = tracker._tracks[1]
    assert track.kalman_filter is None
    assert tuple(track.predicted_bbox(1.0)) == pytest.approx((50, 10, 70, 180))


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


def test_후처리는_겹친_사람_bbox에서_높은_confidence만_남긴다() -> None:
    tracker = CameraByteTracker(config(person_detection_postprocess_enabled=True))

    tracked = tracker.update(
        result(
            person(0.72, (10, 10, 80, 180)),
            person(0.91, (12, 10, 82, 180)),
        )
    )

    assert len(tracked.detections) == 1
    assert tracked.detections[0].confidence == 0.91
    assert tracked.detections[0].track_id == "person-1"


def test_후처리는_포함_관계에서_confidence보다_큰_bbox를_남긴다() -> None:
    tracker = CameraByteTracker(config(person_detection_postprocess_enabled=True))
    large = person(0.65, (10, 10, 110, 190))
    small = person(0.95, (20, 20, 100, 180))

    tracked = tracker.update(result(small, large))

    assert tuple(item.bbox for item in tracked.detections) == (large.bbox,)


def test_후처리_완전_동률은_입력_순서를_유지한다() -> None:
    tracker = CameraByteTracker(config(person_detection_postprocess_enabled=True))
    first = person(0.9, (10, 10, 80, 180))
    second = person(0.9, (12, 10, 82, 180))

    tracked = tracker.update(result(first, second))

    assert tuple(item.bbox for item in tracked.detections) == (first.bbox,)


def test_후처리는_기존_track과_미매칭한_저신뢰_사람을_제거한다() -> None:
    tracker = CameraByteTracker(config(person_detection_postprocess_enabled=True))

    tracked = tracker.update(result(person(0.3, (10, 10, 80, 180))))

    assert tracked.detections == ()
    assert tracker.active_track_count == 0


def test_후처리는_기존_track과_매칭한_저신뢰_사람을_유지한다() -> None:
    tracker = CameraByteTracker(config(person_detection_postprocess_enabled=True))
    tracker.update(result(person(0.9, (10, 10, 80, 180))))

    tracked = tracker.update(result(person(0.3, (13, 10, 83, 180))))

    assert len(tracked.detections) == 1
    assert tracked.detections[0].track_id == "person-1"


def test_후처리는_미매칭이어도_confidence_0_50_이상이면_유지한다() -> None:
    tracker = CameraByteTracker(config(person_detection_postprocess_enabled=True))

    tracked = tracker.update(result(person(0.55, (10, 10, 80, 180))))

    assert len(tracked.detections) == 1
    assert tracked.detections[0].track_id is None


def test_후처리는_다른_클래스를_변경하지_않는다() -> None:
    tracker = CameraByteTracker(config(person_detection_postprocess_enabled=True))
    phone = Detection(67, "cell phone", 0.2, (10, 10, 80, 180))

    tracked = tracker.update(
        result(phone, person(0.3, (10, 10, 80, 180)))
    )

    assert tracked.detections == (phone,)


def test_후처리를_끄면_기존_중복과_저신뢰_출력을_유지한다() -> None:
    tracker = CameraByteTracker(config(person_detection_postprocess_enabled=False))

    tracked = tracker.update(
        result(
            person(0.3, (10, 10, 80, 180)),
            person(0.3, (12, 10, 82, 180)),
        )
    )

    assert len(tracked.detections) == 2


def test_후처리_결과를_오버레이와_이벤트_분기에_동일하게_전달한다() -> None:
    overlay: list[InferenceResult] = []
    event: list[InferenceResult] = []

    def fanout(_frame: CapturedFrame, value: InferenceResult) -> None:
        overlay.append(value)
        event.append(value)

    handler = ByteTrackResultHandler(
        config(person_detection_postprocess_enabled=True), inner=fanout
    )
    handler(
        captured("classroom-cctv", 0),
        result(
            person(0.9, (10, 10, 80, 180)),
            person(0.7, (12, 10, 82, 180)),
            person(0.3, (150, 10, 220, 180)),
        ),
    )

    assert overlay[0] == event[0]
    assert len(overlay[0].detections) == 1


def test_익명_trace_fixture의_중복은_제거하고_정상_recall은_유지한다() -> None:
    fixture = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "person_detection_trace.jsonl"
    )
    records = [
        json.loads(line)
        for line in fixture.read_text(encoding="utf-8").splitlines()
        if line
    ]
    tracker = CameraByteTracker(config(person_detection_postprocess_enabled=True))
    processed_counts: list[int] = []
    for record in records:
        if record["record_type"] != "frame":
            continue
        detections = tuple(
            person(item["confidence"], tuple(item["bbox"]))
            for item in record["person_detections"]
        )
        processed = tracker.update(
            InferenceResult(tuple(record["frame_shape"]), detections),
            observed_at=record["elapsed_ms"] / 1000.0,
        )
        processed_counts.append(len(processed.detections))

    # fixture의 첫 프레임은 duplicate-1 두 건이 한 사람이고 둘째는 정상 한 건이다.
    expected_unique_detections = 2
    assert processed_counts == [1, 1]
    recall_decrease = 1.0 - sum(processed_counts) / expected_unique_detections
    assert recall_decrease <= 0.05


def test_짧은_미탐_뒤에도_이동_예측으로_track을_회복한다() -> None:
    tracker = CameraByteTracker(config(track_buffer_frames=3))
    tracker.update(result(person(0.9, (10, 10, 60, 180))))
    tracker.update(result(person(0.9, (20, 10, 70, 180))))
    tracker.update(result())

    recovered = tracker.update(result(person(0.9, (40, 10, 90, 180))))

    assert recovered.detections[0].track_id == "person-1"


def test_Kalman은_짧은_미탐_뒤에도_track을_회복한다() -> None:
    tracker = CameraByteTracker(config(track_buffer_frames=3, kalman_enabled=True))
    tracker.update(result(person(0.9, (10, 10, 60, 180))), observed_at=0.0)
    tracker.update(result(person(0.9, (20, 10, 70, 180))), observed_at=0.2)
    tracker.update(result(), observed_at=0.4)

    recovered = tracker.update(
        result(person(0.9, (40, 10, 90, 180))), observed_at=0.6
    )

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
