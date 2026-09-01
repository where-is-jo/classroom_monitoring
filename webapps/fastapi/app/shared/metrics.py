"""fastapi가 Prometheus 지표를 노출하는 공용 기반.

지표 이름 접두사와 렌더링만 여기 둔다. **기능별 지표 정의는 각 기능 디렉터리에**
둔다(`app/llm_search/metrics.py`). 한곳에 몰면 기능을 지울 때 지표만 남는다.

## 포트를 만들지 않는 이유

`prometheus_client`는 값을 프로세스 메모리에 쌓기만 하고, 스크랩이 들어올 때까지
바깥으로 나가지 않는다. "프로세스 밖으로 나가는 I/O인가"에 아니오라서 포트를 두는
네 경계(저장소·추론 클라이언트·객체 저장소·알림) 어디에도 해당하지 않는다.
서비스 계층이 `logging`을 직접 쓰는 것과 같은 자리다.

## 다중 프로세스에서는 값이 갈라진다

uvicorn을 워커 여러 개로 띄우면 프로세스마다 레지스트리가 따로 생기고, 스크랩은
그중 하나에만 닿는다. 지금은 단일 프로세스로 실행하므로 문제가 없지만,
**배포 방식이 `결정 필요`**(docs/architecture/README.md)라 워커를 늘리는 순간
`prometheus_client`의 multiprocess 모드를 켜야 한다는 것을 여기 적어 둔다.
"""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, generate_latest

__all__ = ["METRIC_PREFIX", "render_metrics"]

METRIC_PREFIX = "classroom_monitoring_"


def render_metrics() -> tuple[bytes, str]:
    """지금까지 쌓인 지표를 Prometheus 텍스트 형식으로 만든다.

    본문과 content type을 함께 돌려준다. content type에는 버전 정보가 들어 있어서
    직접 적으면 라이브러리를 올릴 때 어긋난다.
    """
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
