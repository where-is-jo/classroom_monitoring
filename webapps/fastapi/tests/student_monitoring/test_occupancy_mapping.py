"""ROI 기반 좌석 점유 매핑 테스트 (결정 0020).

핵심은 관측 범위다. 강의실을 나눠 보는 구성에서 카메라가 자기 담당이 아닌
좌석까지 "비어 있음"으로 기록하면 다른 카메라의 관측을 지운다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from app.roi_connections.models import Point, RoiConnection
from app.student_monitoring.models import Detection, FrameInfo
from app.student_monitoring.occupancy_mapping import (
    map_detections_to_evidence,
    unseated_identities,
)

NOW = datetime(2026, 8, 16, 3, 0, tzinfo=UTC)
FRAME = FrameInfo(width_pixels=1000, height_pixels=1000)
THRESHOLD = 0.6


def make_connection(seat_id: str, x_min: float, x_max: float) -> RoiConnection:
    """세로 전체를 덮고 가로만 나누는 단순한 ROI."""
    return RoiConnection(
        classroom_id="classroom-a101",
        camera_id="camera-left",
        seat_id=seat_id,
        student_id=None,
        polygon=(
            Point(x=x_min, y=0.0),
            Point(x=x_max, y=0.0),
            Point(x=x_max, y=1.0),
            Point(x=x_min, y=1.0),
        ),
        reference_image_revision=1,
        updated_at=NOW,
    )


def make_detection(
    detection_id: str,
    bbox: tuple[int, int, int, int],
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


SEAT_A = make_connection("seat-a", 0.0, 0.3)
SEAT_B = make_connection("seat-b", 0.35, 0.65)


class TestObservationScope:
    def test_only_seats_registered_for_this_camera_are_observed(self) -> None:
        """ROI가 등록된 좌석만 관측한다. 담당 밖 좌석은 결과에 없다."""
        observations = map_detections_to_evidence(
            [make_detection("d1", (100, 400, 200, 600))],  # 중심 (0.15, 0.5) = seat-a
            [SEAT_A, SEAT_B],
            FRAME,
            THRESHOLD,
        )

        assert {o.seat_id for o in observations} == {"seat-a", "seat-b"}

    def test_no_connections_produces_no_observations(self) -> None:
        """ROI가 없으면 좌석을 추정하지 않고 아무 관측도 만들지 않는다."""
        observations = map_detections_to_evidence(
            [make_detection("d1", (100, 400, 200, 600))],
            [],
            FRAME,
            THRESHOLD,
        )

        assert observations == ()

    def test_duplicate_seat_connections_yield_one_observation(self) -> None:
        """같은 좌석 ROI가 둘이어도 관측은 좌석당 하나다."""
        observations = map_detections_to_evidence(
            [],
            [SEAT_A, make_connection("seat-a", 0.7, 0.9)],
            FRAME,
            THRESHOLD,
        )

        assert len(observations) == 1
        assert observations[0].seat_id == "seat-a"


class TestOccupancyDecision:
    def test_detection_inside_roi_marks_seat_occupied(self) -> None:
        observations = map_detections_to_evidence(
            [make_detection("d1", (100, 400, 200, 600))],  # 중심 (0.15, 0.5)
            [SEAT_A, SEAT_B],
            FRAME,
            THRESHOLD,
        )

        by_seat = {o.seat_id: o for o in observations}
        assert by_seat["seat-a"].occupied is True
        assert by_seat["seat-a"].confidence == 0.9
        assert by_seat["seat-b"].occupied is False

    def test_detection_outside_every_roi_marks_all_vacant(self) -> None:
        """어느 ROI에도 없는 bbox는 점유 증거가 되지 않는다."""
        observations = map_detections_to_evidence(
            [make_detection("d1", (800, 400, 900, 600))],  # 중심 (0.85, 0.5)
            [SEAT_A, SEAT_B],
            FRAME,
            THRESHOLD,
        )

        assert all(o.occupied is False for o in observations)

    def test_low_confidence_detection_is_uncertain_not_vacant(self) -> None:
        """임계값 미만 탐지가 잡힌 좌석을 "비었다"로 단정하지 않는다.

        낮은 신뢰도를 그대로 넘겨 좌석 서비스가 `UNKNOWN`으로 해석하게 한다.
        버리면 매칭 없음이 되어 `VACANT`로 기록되고, 그것은 조용한 오판이다.
        """
        observations = map_detections_to_evidence(
            [make_detection("d1", (100, 400, 200, 600), confidence=0.5)],
            [SEAT_A],
            FRAME,
            THRESHOLD,
        )

        assert observations[0].occupied is True
        assert observations[0].confidence == 0.5

    def test_seat_without_any_detection_is_vacant(self) -> None:
        """관측 대상 좌석에 아무 탐지도 없으면 "관측했고 비어 있음"이다."""
        observations = map_detections_to_evidence(
            [],
            [SEAT_A],
            FRAME,
            THRESHOLD,
        )

        assert observations[0].occupied is False
        assert observations[0].confidence == 0.0

    def test_overlapping_people_keep_the_highest_confidence(self) -> None:
        observations = map_detections_to_evidence(
            [
                make_detection("d1", (100, 400, 200, 600), confidence=0.7),
                make_detection("d2", (110, 410, 210, 610), confidence=0.95),
            ],
            [SEAT_A],
            FRAME,
            THRESHOLD,
        )

        assert observations[0].occupied is True
        assert observations[0].confidence == 0.95

    def test_ambiguous_overlapping_rois_are_not_evidence(self) -> None:
        """겹치는 ROI 두 곳에 걸린 bbox로는 좌석을 정하지 않는다."""
        overlapping = make_connection("seat-b", 0.0, 0.3)

        observations = map_detections_to_evidence(
            [make_detection("d1", (100, 400, 200, 600))],
            [SEAT_A, overlapping],
            FRAME,
            THRESHOLD,
        )

        assert all(o.occupied is False for o in observations)


def test_쓸_수_있는_ROI가_없으면_좌석_밖_식별을_만들지_않는다() -> None:
    """좌석 지도가 없는 카메라에서 "좌석 밖에 있다"를 말할 수 없다.

    ROI를 등록하지 않았거나 전부 `needs_review`로 떨어진 카메라에서는 모든 탐지가
    NO_MATCH다. 그것을 좌석 밖으로 세면 ROI 미등록이 "자리에 없다"는 판정으로 둔갑해,
    실제로 앉아 있는 학생이 IN_CLASSROOM으로 기록된다.
    """
    identified = make_detection("d1", (100, 400, 200, 600), confidence=0.9)
    identified = replace(identified, student_id="student-1", identity_confidence=0.9)

    assert unseated_identities([identified], [], FRAME, THRESHOLD) == {}
    # 좌석 지도가 있으면 같은 탐지가 좌석 밖 식별로 잡힌다.
    assert unseated_identities([identified], [SEAT_B], FRAME, THRESHOLD) == {"student-1": 0.9}
