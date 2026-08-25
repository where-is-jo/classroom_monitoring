"""워커가 Prometheus 지표를 노출하는 공용 기반.

여기 두는 것은 두 가지다.

- **지표 노출 경로** — 워커는 웹 서버가 아니라서 `/metrics`를 열어 줄 곳이 없다.
  `start_metrics_server`가 그 자리를 만든다.
- **프레임 버퍼 지표** — `FrameBuffer`는 stream과 inference 사이의 계약이라
  어느 한쪽 워커에 지표를 두면 다른 쪽이 그 워커를 import하게 된다. shared에 버퍼를
  둔 이유가 그대로 지표에도 적용된다.

워커 하나만 쓰는 지표는 여기 넣지 않는다. 추론 지표는 `inference/metrics.py`에 있다.

## 버퍼 지표를 왜 collector로 노출하는가

`FrameBuffer`는 이미 accepted·dropped·consumed·skipped를 정확히 세고 있다
(`FrameBufferStats`). 같은 값을 Counter로 한 번 더 세면 **두 숫자가 어긋날 수 있고**,
`put`·`get_latest`는 카메라 스레드가 프레임마다 지나는 자리라 락 안에서 하는 일을
늘리고 싶지 않다. 그래서 스크랩이 들어온 시점에 `stats`를 한 번 읽어 내보낸다.
이 방식 덕분에 `frame_buffer.py`는 이 변경으로 한 줄도 바뀌지 않는다.

## 이름 규칙

`classroom_monitoring_` 접두사를 쓴다(`monitoring/internal/README.md`). Counter는
`_total`로 끝나며, 이 접미사는 prometheus_client가 붙여 준다 — 코드에 적은 이름과
실제로 노출되는 이름이 다르지 않도록 여기서는 `_total`까지 적고 넘긴다.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence

from prometheus_client import REGISTRY, CollectorRegistry, start_http_server
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, Metric
from prometheus_client.registry import Collector

from .frame_buffer import FrameBuffer

logger = logging.getLogger(__name__)

__all__ = [
    "METRIC_PREFIX",
    "FrameBufferCollector",
    "register_frame_buffer",
    "start_metrics_server",
]

METRIC_PREFIX = "classroom_monitoring_"


class FrameBufferCollector(Collector):
    """스크랩 시점에 `FrameBuffer.stats`를 읽어 지표로 내보낸다.

    노출하는 값은 네 가지다.

    | 지표 | 타입 | 의미 |
    | --- | --- | --- |
    | `classroom_monitoring_frames_buffered_total` | Counter | 버퍼에 들어간 프레임 |
    | `classroom_monitoring_frames_dropped_total{reason}` | Counter | 추론에 닿지 못한 프레임 |
    | `classroom_monitoring_frames_consumed_total` | Counter | 소비자가 가져간 프레임 |
    | `classroom_monitoring_frame_buffer_depth` | Gauge | 지금 버퍼에 남아 있는 수 |

    `reason`은 `dropped`(자리를 만들려고 버림)와 `skipped`(꺼낼 때 최신이 아니어서
    건너뜀) 두 값뿐이다. **둘을 합치지 않는 이유는 원인이 다르기 때문이다** —
    dropped는 버퍼가 가득 찼다는 뜻이고 skipped는 한 번에 여러 장이 밀려 있었다는
    뜻이다. `maxsize=1`인 기본 설정에서는 skipped가 거의 나오지 않아야 정상이다.
    """

    def __init__(self, frame_buffer: FrameBuffer | Sequence[FrameBuffer]) -> None:
        self._frame_buffers = (
            (frame_buffer,)
            if isinstance(frame_buffer, FrameBuffer)
            else tuple(frame_buffer)
        )
        if not self._frame_buffers:
            raise ValueError("지표를 등록할 프레임 버퍼가 하나 이상 필요합니다.")

    def collect(self) -> Iterator[Metric]:
        stats = [frame_buffer.stats for frame_buffer in self._frame_buffers]

        yield CounterMetricFamily(
            f"{METRIC_PREFIX}frames_buffered",
            "stream이 버퍼에 넣는 데 성공한 프레임 수",
            value=sum(value.accepted for value in stats),
        )

        dropped = CounterMetricFamily(
            f"{METRIC_PREFIX}frames_dropped",
            "추론에 닿지 못하고 버려진 프레임 수. 추론이 수신을 못 따라간 양이다",
            labels=["reason"],
        )
        dropped.add_metric(["dropped"], sum(value.dropped for value in stats))
        dropped.add_metric(["skipped"], sum(value.skipped for value in stats))
        yield dropped

        yield CounterMetricFamily(
            f"{METRIC_PREFIX}frames_consumed",
            "소비자가 버퍼에서 꺼내 간 프레임 수",
            value=sum(value.consumed for value in stats),
        )

        yield GaugeMetricFamily(
            f"{METRIC_PREFIX}frame_buffer_depth",
            "지금 버퍼에 남아 있는 프레임 수",
            value=sum(len(frame_buffer) for frame_buffer in self._frame_buffers),
        )


def register_frame_buffer(
    frame_buffer: FrameBuffer | Sequence[FrameBuffer],
    *,
    registry: CollectorRegistry = REGISTRY,
) -> FrameBufferCollector:
    """버퍼 지표를 레지스트리에 등록한다.

    **조립 지점에서 한 번만 부른다.** 같은 레지스트리에 두 번 등록하면
    prometheus_client가 중복으로 거부한다. 테스트는 자기 레지스트리를 넘겨
    전역 레지스트리를 건드리지 않는다.
    """
    collector = FrameBufferCollector(frame_buffer)
    registry.register(collector)
    return collector


def start_metrics_server(*, host: str, port: int) -> bool:
    """`/metrics`를 여는 HTTP 서버를 띄운다. 데몬 스레드로 돈다.

    **실패해도 예외를 밖으로 내지 않는다.** 포트가 이미 쓰이고 있다고 해서 영상
    수신과 추론이 멈출 이유는 없다. 관측 수단이 없어진 것이지 기능이 고장 난 것이
    아니다. 대신 조용히 넘기지 않고 오류로 남긴다.

    반환값은 서버가 떴는지다. 호출자가 로그 외에 판단할 일이 있으면 쓴다.
    """
    try:
        # prometheus_client가 내부에서 데몬 스레드를 띄운다. 여기서 join하지 않는다.
        start_http_server(port, addr=host)
    except OSError as error:
        logger.error(
            "지표 노출 서버를 열지 못했다(%s:%d): %s. 워커는 그대로 계속 돈다.",
            host,
            port,
            error,
        )
        return False

    logger.info("지표를 http://%s:%d/metrics 에 노출한다", host, port)
    return True
