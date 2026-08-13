"""탐지→좌석 매핑 단위 테스트."""

from __future__ import annotations

from datetime import UTC, datetime

from app.classrooms.mapping import (
    bbox_center,
    find_seat_for_detection,
    map_detections_to_observations,
)
from app.classrooms.models import (
    OccupancySource,
    Seat,
    SeatCurrentOccupancy,
    SeatGeometry,
    SeatObservation,
    SeatOccupancy,
)
from app.student_monitoring.models import Detection, FrameInfo


def _seat(seat_id: str, *, x: float, y: float, width: float, height: float) -> Seat:
    return Seat(
        id=seat_id,
        classroom_id="classroom-a101",
        code=seat_id.upper(),
        label=seat_id,
        geometry=SeatGeometry(x=x, y=y, width=width, height=height),
        is_active=True,
        current_occupancy=SeatCurrentOccupancy(
            state=SeatOccupancy.UNKNOWN,
            source=OccupancySource.SYSTEM,
            confidence=None,
            observed_at=None,
            event_id=None,
        ),
        created_at=datetime(2026, 8, 13, 9, 0, tzinfo=UTC),
        updated_at=datetime(2026, 8, 13, 9, 0, tzinfo=UTC),
        version=0,
    )


def _seat_without_geometry(seat_id: str) -> Seat:
    return Seat(
        id=seat_id,
        classroom_id="classroom-a101",
        code=seat_id.upper(),
        label=seat_id,
        geometry=None,
        is_active=True,
        current_occupancy=SeatCurrentOccupancy(
            state=SeatOccupancy.UNKNOWN,
            source=OccupancySource.SYSTEM,
            confidence=None,
            observed_at=None,
            event_id=None,
        ),
        created_at=datetime(2026, 8, 13, 9, 0, tzinfo=UTC),
        updated_at=datetime(2026, 8, 13, 9, 0, tzinfo=UTC),
        version=0,
    )


def _detection(
    detection_id: str,
    bbox: tuple[int, int, int, int],
    *,
    confidence: float = 0.9,
) -> Detection:
    return Detection(
        detection_id=detection_id,
        class_id=0,
        class_name="person",
        confidence=confidence,
        bbox=bbox,
        student_id=None,
        identity_confidence=None,
        face_bbox=None,
    )


FRAME = FrameInfo(width_pixels=1000, height_pixels=1000)
# A: 정규화 영역 [0.1, 0.3]x[0.1, 0.3], B: [0.5, 0.7]x[0.1, 0.3]
SEAT_A = _seat("seat-a", x=0.1, y=0.1, width=0.2, height=0.2)
SEAT_B = _seat("seat-b", x=0.5, y=0.1, width=0.2, height=0.2)
# 중심 (200, 200) → norm (0.2, 0.2) → seat-a
DET_IN_A = _detection("det-a", (150, 150, 250, 250))
# 중심 (850, 850) → norm (0.85, 0.85) → 어떤 좌석에도 없음
DET_OUTSIDE = _detection("det-out", (800, 800, 900, 900))


class TestBBoxCenter:
    def test_center_of_bbox(self) -> None:
        assert bbox_center((100, 200, 300, 400)) == (200.0, 300.0)

    def test_center_is_float_when_sum_is_odd(self) -> None:
        assert bbox_center((101, 201, 302, 402)) == (201.5, 301.5)


class TestFindSeatForDetection:
    def test_detection_inside_seat(self) -> None:
        seat = find_seat_for_detection(DET_IN_A, (SEAT_A, SEAT_B), 1000, 1000)
        assert seat is not None
        assert seat.id == "seat-a"

    def test_detection_outside_all_seats(self) -> None:
        seat = find_seat_for_detection(DET_OUTSIDE, (SEAT_A, SEAT_B), 1000, 1000)
        assert seat is None

    def test_center_on_boundary_counts_inside(self) -> None:
        # 중심점이 좌석 왼쪽 위 경계(0.1) 위에 있으면 좌석 안으로 본다.
        detection = _detection("det-edge", (80, 80, 120, 120))
        seat = find_seat_for_detection(detection, (SEAT_A,), 1000, 1000)
        assert seat is not None
        assert seat.id == "seat-a"

    def test_seat_without_geometry_is_skipped(self) -> None:
        seat = find_seat_for_detection(DET_IN_A, (_seat_without_geometry("seat-x"),), 1000, 1000)
        assert seat is None

    def test_straddling_seats_picks_first_seat_containing_center(self) -> None:
        # A [0.1, 0.3]와 B' [0.2, 0.4]가 겹치고 중심점은 (0.25, 0.2) → 목록상 먼저 맞는 seat-a
        overlapping_b = _seat("seat-b-overlap", x=0.2, y=0.1, width=0.2, height=0.2)
        detection = _detection("det-mid", (230, 190, 270, 210))
        seat = find_seat_for_detection(detection, (SEAT_A, overlapping_b), 1000, 1000)
        assert seat is not None
        assert seat.id == "seat-a"


class TestMapDetectionsToObservations:
    def test_maps_detection_to_its_seat(self) -> None:
        observations = map_detections_to_observations(
            (DET_IN_A,), (SEAT_A, SEAT_B), FRAME, confidence_threshold=0.5
        )
        assert observations == (
            SeatObservation(seat_id="seat-a", occupied=True, confidence=0.9),
            SeatObservation(seat_id="seat-b", occupied=False, confidence=0.0),
        )

    def test_multiple_people_overlapping_picks_highest_confidence(self) -> None:
        low = _detection("det-low", (150, 150, 250, 250), confidence=0.6)
        high = _detection("det-high", (140, 140, 260, 260), confidence=0.95)
        observations = map_detections_to_observations(
            (low, high), (SEAT_A,), FRAME, confidence_threshold=0.5
        )
        assert observations == (SeatObservation(seat_id="seat-a", occupied=True, confidence=0.95),)

    def test_detection_outside_all_seats_leaves_seats_unknown(self) -> None:
        observations = map_detections_to_observations(
            (DET_OUTSIDE,), (SEAT_A, SEAT_B), FRAME, confidence_threshold=0.5
        )
        assert observations == (
            SeatObservation(seat_id="seat-a", occupied=False, confidence=0.0),
            SeatObservation(seat_id="seat-b", occupied=False, confidence=0.0),
        )

    def test_empty_detections_marks_all_seats_unknown(self) -> None:
        observations = map_detections_to_observations(
            (), (SEAT_A, SEAT_B), FRAME, confidence_threshold=0.5
        )
        assert observations == (
            SeatObservation(seat_id="seat-a", occupied=False, confidence=0.0),
            SeatObservation(seat_id="seat-b", occupied=False, confidence=0.0),
        )

    def test_seat_without_geometry_is_excluded(self) -> None:
        observations = map_detections_to_observations(
            (DET_IN_A,),
            (SEAT_A, _seat_without_geometry("seat-x")),
            FRAME,
            confidence_threshold=0.5,
        )
        assert observations == (SeatObservation(seat_id="seat-a", occupied=True, confidence=0.9),)

    def test_detection_below_confidence_threshold_is_ignored(self) -> None:
        low = _detection("det-low", (150, 150, 250, 250), confidence=0.3)
        observations = map_detections_to_observations(
            (low,), (SEAT_A,), FRAME, confidence_threshold=0.5
        )
        assert observations == (SeatObservation(seat_id="seat-a", occupied=False, confidence=0.0),)
