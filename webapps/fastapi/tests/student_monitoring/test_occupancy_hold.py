"""좌석 점유 유지(hold) 규칙.

앉아 있는 사람도 프레임마다 꾸준히 잡히지 않는다. 실측에서 미탐 구간 24개 중 14개가
1프레임(1.3초)짜리였다. 그것을 그대로 "비어 있음"으로 기록하면 좌석 상태가 몇 초마다
깜빡인다. 반대로 너무 오래 붙들면 자리를 뜬 사람을 계속 앉아 있다고 기록한다.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.classrooms.models import SeatObservation
from app.roi_connections.models import Point, RoiConnection
from app.student_monitoring.models import Detection, FrameInfo
from app.student_monitoring.occupancy_mapping import map_detections_to_observations

NOW = datetime(2026, 8, 21, 9, 0, tzinfo=UTC)
FRAME = FrameInfo(width_pixels=1000, height_pixels=1000)


def _roi(seat_id: str, x0: float, x1: float) -> RoiConnection:
    return RoiConnection(
        classroom_id="room",
        camera_id="camera-a",
        seat_id=seat_id,
        student_id=None,
        polygon=(Point(x0, 0.4), Point(x1, 0.4), Point(x1, 0.6), Point(x0, 0.6)),
        reference_image_revision=1,
        updated_at=NOW,
    )


def _person(bbox: tuple[int, int, int, int], confidence: float = 0.8) -> Detection:
    return Detection(
        detection_id="d1",
        class_id=0,
        class_name="person",
        confidence=confidence,
        bbox=bbox,
        student_id=None,
        identity_confidence=None,
        face_bbox=None,
    )


def _states(observations: tuple[SeatObservation, ...]) -> dict[str, bool]:
    return {o.seat_id: o.occupied for o in observations}


def test_seat_is_vacant_without_hold() -> None:
    """붙들 근거가 없으면 이전과 같이 비어 있음으로 본다."""
    observations = map_detections_to_observations([], [_roi("a", 0.1, 0.3)], FRAME, 0.3)

    assert _states(observations) == {"a": False}


def test_held_seat_stays_occupied_when_the_frame_misses_it() -> None:
    observations = map_detections_to_observations(
        [], [_roi("a", 0.1, 0.3)], FRAME, 0.3, held={"a": 0.72}
    )

    assert _states(observations) == {"a": True}


def test_held_seat_keeps_the_confidence_it_was_last_seen_with() -> None:
    """새로 본 것이 아니므로 신뢰도를 올리지 않는다."""
    observations = map_detections_to_observations(
        [], [_roi("a", 0.1, 0.3)], FRAME, 0.3, held={"a": 0.42}
    )

    assert observations[0].confidence == 0.42


def test_a_seat_detected_now_uses_the_fresh_confidence_not_the_held_one() -> None:
    observations = map_detections_to_observations(
        [_person((150, 450, 250, 550), confidence=0.9)],
        [_roi("a", 0.1, 0.3)],
        FRAME,
        0.3,
        held={"a": 0.42},
    )

    assert observations[0].occupied is True
    assert observations[0].confidence == 0.9


def test_hold_does_not_leak_to_other_seats() -> None:
    """붙드는 것은 그 좌석에 한정된다. 옆자리까지 점유로 만들지 않는다."""
    observations = map_detections_to_observations(
        [], [_roi("a", 0.1, 0.3), _roi("b", 0.5, 0.7)], FRAME, 0.3, held={"a": 0.6}
    )

    assert _states(observations) == {"a": True, "b": False}
