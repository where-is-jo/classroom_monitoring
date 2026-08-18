"""프레임 버퍼 지표 노출 검증. Prometheus 서버 없이 돈다."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
from prometheus_client import CollectorRegistry

from ..frame_buffer import FrameBuffer
from ..metrics import METRIC_PREFIX, FrameBufferCollector, register_frame_buffer, start_metrics_server
from ..types import CapturedFrame


def make_captured(sequence: int) -> CapturedFrame:
    return CapturedFrame(
        camera_id="camera-01",
        frame=np.zeros((2, 2, 3), dtype=np.uint8),
        captured_at=datetime(2026, 8, 18, 9, 0, 0, tzinfo=UTC),
        sequence=sequence,
    )


def sample_value(collector: FrameBufferCollector, name: str, **labels: str) -> float:
    """`name` 지표의 값을 꺼낸다. 없으면 실패하도록 KeyError를 낸다."""
    for metric in collector.collect():
        for sample in metric.samples:
            if sample.name == f"{METRIC_PREFIX}{name}" and sample.labels == labels:
                return float(sample.value)
    raise KeyError(f"{name} 지표를 찾지 못했다")


def test_버퍼가_비어_있어도_지표가_모두_나온다() -> None:
    collector = FrameBufferCollector(FrameBuffer(maxsize=1))

    assert sample_value(collector, "frames_buffered_total") == 0
    assert sample_value(collector, "frames_consumed_total") == 0
    assert sample_value(collector, "frame_buffer_depth") == 0
    assert sample_value(collector, "frames_dropped_total", reason="dropped") == 0
    assert sample_value(collector, "frames_dropped_total", reason="skipped") == 0


def test_버퍼_통계를_그대로_내보낸다() -> None:
    buffer = FrameBuffer(maxsize=1)
    collector = FrameBufferCollector(buffer)

    buffer.put(make_captured(1))
    buffer.put(make_captured(2))  # 자리를 만들려고 1번을 버린다

    assert sample_value(collector, "frames_buffered_total") == 2
    assert sample_value(collector, "frames_dropped_total", reason="dropped") == 1
    assert sample_value(collector, "frame_buffer_depth") == 1

    buffer.get_latest(timeout=0)

    assert sample_value(collector, "frames_consumed_total") == 1
    assert sample_value(collector, "frame_buffer_depth") == 0


def test_버린_이유를_구분해_센다() -> None:
    """dropped와 skipped는 원인이 다르므로 합치지 않는다."""
    buffer = FrameBuffer(maxsize=3)
    collector = FrameBufferCollector(buffer)

    for sequence in range(3):
        buffer.put(make_captured(sequence))
    buffer.get_latest(timeout=0)  # 최신 한 장만 가져가고 나머지 2장은 건너뛴다

    assert sample_value(collector, "frames_dropped_total", reason="dropped") == 0
    assert sample_value(collector, "frames_dropped_total", reason="skipped") == 2


def test_스크랩할_때마다_최신_값을_읽는다() -> None:
    """collector는 값을 자기가 세지 않는다. 버퍼가 정본이다."""
    buffer = FrameBuffer(maxsize=1)
    collector = FrameBufferCollector(buffer)

    assert sample_value(collector, "frames_buffered_total") == 0
    buffer.put(make_captured(1))
    assert sample_value(collector, "frames_buffered_total") == 1


def test_레지스트리에_등록하면_수집_대상이_된다() -> None:
    registry = CollectorRegistry()
    buffer = FrameBuffer(maxsize=1)
    register_frame_buffer(buffer, registry=registry)

    buffer.put(make_captured(1))

    assert registry.get_sample_value(f"{METRIC_PREFIX}frames_buffered_total") == 1


def test_지표_서버를_열지_못해도_예외를_내지_않는다() -> None:
    """관측 수단이 없어진 것이지 파이프라인이 고장 난 것이 아니다."""
    assert start_metrics_server(host="해석할 수 없는 호스트", port=9101) is False
