"""탐지 밀도에서 좌석 자리를 찾는 순수 규칙 테스트.

카메라도 저장소도 없이 돈다. 합성 표본으로 규칙의 경계를 확인한다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.roi_connections.detection_layout import (
    MIN_POLYGON_SAMPLES,
    DetectionSample,
    plan_detection_rois,
)
from app.roi_connections.errors import RoiConnectionInputError
from app.roi_connections.mapping import map_bbox_to_roi
from app.roi_connections.models import Point, RoiConnection

START = datetime(2026, 8, 25, 9, 0, tzinfo=UTC)


def seated(
    track_id: str,
    x: float,
    y: float,
    *,
    count: int = 200,
    jitter: float = 0.004,
    start: datetime = START,
    step_seconds: float = 1.0,
) -> list[DetectionSample]:
    """한 자리에 앉아 미세하게만 흔들리는 사람의 탐지를 만든다."""
    samples = []
    for index in range(count):
        # 결정적인 흔들림. 난수를 쓰면 실패가 재현되지 않는다.
        offset = (index % 5 - 2) * jitter
        samples.append(
            DetectionSample(
                x=x + offset,
                y=y + offset / 2,
                track_id=track_id,
                captured_at=start + timedelta(seconds=index * step_seconds),
            )
        )
    return samples


def walking(track_id: str, *, count: int = 200) -> list[DetectionSample]:
    """화면을 가로지르는 사람. 좌석이 되면 안 된다."""
    return [
        DetectionSample(
            x=0.05 + index / count * 0.9,
            y=0.5,
            track_id=track_id,
            captured_at=START + timedelta(seconds=index),
        )
        for index in range(count)
    ]


def _covers(polygon: tuple[Point, ...], x: float, y: float) -> bool:
    scale = 100_000
    center_x, center_y = int(x * scale), int(y * scale)
    result = map_bbox_to_roi(
        (center_x - 1, center_y - 1, center_x + 1, center_y + 1),
        frame_width_pixels=scale,
        frame_height_pixels=scale,
        connections=[
            RoiConnection(
                classroom_id="room",
                camera_id="camera-a",
                seat_id="probe",
                student_id=None,
                polygon=polygon,
                reference_image_revision=0,
                updated_at=START,
            )
        ],
    )
    return result.connection is not None


def test_seated_people_become_one_spot_each() -> None:
    samples = [
        *seated("person-1", 0.25, 0.30),
        *seated("person-2", 0.65, 0.30),
        *seated("person-3", 0.45, 0.70),
    ]

    plan = plan_detection_rois(samples, max_clusters=20)

    assert len(plan.clusters) == 3
    assert plan.stationary_count > 0
    for cluster in plan.clusters:
        assert cluster.sample_count >= MIN_POLYGON_SAMPLES
        assert len(cluster.polygon) >= 3


def test_each_spot_covers_the_place_people_actually_sat() -> None:
    """자리를 찾아도 그 점이 ROI 밖이면 판정에서 빗나간다."""
    samples = [*seated("person-1", 0.25, 0.30), *seated("person-2", 0.70, 0.60)]

    plan = plan_detection_rois(samples, max_clusters=20)

    assert len(plan.clusters) == 2
    assert any(_covers(cluster.polygon, 0.25, 0.30) for cluster in plan.clusters)
    assert any(_covers(cluster.polygon, 0.70, 0.60) for cluster in plan.clusters)


def test_passers_by_do_not_become_seats() -> None:
    """통로를 지나간 사람이 좌석으로 굳으면 빈 자리가 계속 점유로 기록된다."""
    samples = [*seated("person-1", 0.25, 0.30), *walking("person-2", count=400)]

    plan = plan_detection_rois(samples, max_clusters=20)

    assert len(plan.clusters) == 1
    assert not _covers(plan.clusters[0].polygon, 0.5, 0.5)


def test_spots_never_overlap_each_other() -> None:
    """겹치는 ROI에 들어간 bbox는 AMBIGUOUS가 되어 좌석을 정하지 못한다(결정 0020)."""
    samples = [
        *seated("person-1", 0.40, 0.40),
        # 바로 옆에 붙어 앉은 사람. 두 자리가 하나로 뭉치거나 겹치기 쉬운 배치다.
        *seated("person-2", 0.46, 0.40),
    ]

    plan = plan_detection_rois(samples, max_clusters=20)

    connections = [
        RoiConnection(
            classroom_id="room",
            camera_id="camera-a",
            seat_id=f"seat-{index}",
            student_id=None,
            polygon=cluster.polygon,
            reference_image_revision=0,
            updated_at=START,
        )
        for index, cluster in enumerate(plan.clusters)
    ]
    for cluster in plan.clusters:
        center_x = sum(point.x for point in cluster.polygon) / len(cluster.polygon)
        center_y = sum(point.y for point in cluster.polygon) / len(cluster.polygon)
        scale = 100_000
        result = map_bbox_to_roi(
            (
                int(center_x * scale) - 1,
                int(center_y * scale) - 1,
                int(center_x * scale) + 1,
                int(center_y * scale) + 1,
            ),
            frame_width_pixels=scale,
            frame_height_pixels=scale,
            connections=connections,
        )
        # 겹쳤다면 AMBIGUOUS가 되어 connection이 None이 된다.
        assert result.connection is not None


def test_detections_without_a_track_are_ignored() -> None:
    """움직였는지 알 수 없는 탐지를 앉아 있었다고 세지 않는다."""
    samples = [DetectionSample(x=0.3, y=0.3, track_id=None, captured_at=START) for _ in range(500)]

    plan = plan_detection_rois(samples, max_clusters=20)

    assert plan.clusters == ()
    assert plan.stationary_count == 0
    assert plan.sample_count == 500


def test_brief_visits_are_dropped_as_weak() -> None:
    """잠깐 앉았다 간 자리를 좌석으로 만들지 않는다."""
    samples = [
        *seated("person-1", 0.25, 0.30, count=400),
        *seated("person-2", 0.75, 0.70, count=5),
    ]

    plan = plan_detection_rois(samples, max_clusters=20)

    assert len(plan.clusters) == 1
    assert plan.dropped_weak >= 1


def test_max_clusters_caps_the_result() -> None:
    """좌석 수보다 많은 자리를 만들지 않는다."""
    samples = [
        *seated("person-1", 0.20, 0.20),
        *seated("person-2", 0.50, 0.20),
        *seated("person-3", 0.80, 0.20),
    ]

    plan = plan_detection_rois(samples, max_clusters=2)

    assert len(plan.clusters) == 2


def test_no_samples_returns_an_empty_plan_not_an_error() -> None:
    """탐지가 없는 것은 오류가 아니라 "아직 볼 것이 없다"는 사실이다."""
    plan = plan_detection_rois([], max_clusters=20)

    assert plan.clusters == ()
    assert plan.sample_count == 0
    assert plan.stationary_count == 0


def test_max_clusters_must_be_positive() -> None:
    with pytest.raises(RoiConnectionInputError):
        plan_detection_rois([], max_clusters=0)


def test_spots_are_ordered_top_to_bottom() -> None:
    """화면을 위에서 아래로 훑으며 좌석을 짚을 수 있어야 한다."""
    samples = [
        *seated("person-1", 0.50, 0.80),
        *seated("person-2", 0.50, 0.20),
        *seated("person-3", 0.50, 0.50),
    ]

    plan = plan_detection_rois(samples, max_clusters=20)

    centers = [
        sum(point.y for point in cluster.polygon) / len(cluster.polygon)
        for cluster in plan.clusters
    ]
    assert centers == sorted(centers)
