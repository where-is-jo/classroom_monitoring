"""얼굴 분석 서비스가 노출하는 Prometheus 지표.

정의를 한곳에 모으고, `app.py`는 이름 있는 함수로만 부른다. **이 모듈은
prometheus_client 말고는 아무것도 import하지 않는다** — 모델 없이도 시험할 수 있어야
계측 자체에 테스트를 붙일 수 있기 때문이다(`app.py`는 mediapipe·insightface를
module import 시점에 요구한다).

## `metrics`라는 이름 하나로만 import한다

컨테이너는 `uvicorn app:app`으로 뜨고 테스트는 `deeplearning.app`으로 부른다.
`deeplearning.metrics`와 `metrics`를 섞어 import하면 **모듈이 두 번 로드되어 같은
지표를 두 번 등록하려다 죽는다.** 항상 `import metrics`로만 쓴다.

## 왜 이 서비스에 지표가 필요한가

`/internal/face-analysis`는 얼굴 등록 중 **프레임마다** 불리는 실시간 경로다.
여기가 느려지면 사용자는 가이드가 반응하지 않는다고 느낀다. 그런데 느린 쪽이
SCRFD 검출인지 MediaPipe 자세 추정인지 로그로는 알 수 없어서 단계를 나눠 잰다.

세션 Gauge는 성격이 다르다. `_frame_history`와 `_fingerprint_history`는
`DELETE .../sessions/{id}`가 불려야 비워진다. **브라우저가 그냥 창을 닫으면 항목이
남고, 이것은 메모리 누수인데 지금은 관측할 방법이 전혀 없다.** 딕셔너리 길이 하나면
잡힌다.

## label 값 종류 수 (고카디널리티 점검)

| 지표 | label | 예상 값 수 |
| --- | --- | --- |
| `face_analysis_duration_seconds` | `stage` | 4 |
| `face_analysis_requests_total` | `result` | 5 |
| `face_embedding_duration_seconds` | 없음 | 1 |
| `face_embedding_requests_total` | `result` | 6 |
| `face_identification_duration_seconds` | `model` | 2 |
| `face_identification_requests_total` | `model`, `result` | 12 이하 |
| `face_identification_observations_total` | `model`, `result` | 6 |
| `face_analysis_sessions_active` | 없음 | 1 |

**`enrollment_id`를 label로 쓰지 않는다.** 값이 무한히 늘어나고, 얼굴 등록 세션은
특정 학생을 가리키므로 개인을 식별하는 값이다. 지표는 접근 통제가 약한 경로로
노출되기 쉽다(`monitoring/internal/README.md`).

**얼굴 이미지에서 나온 수치(신뢰도·blur·yaw)를 지표로 내보내지 않는다.** 등록 화면이
이미 응답으로 돌려주는 값이고, 지표로 만들면 개인의 촬영 상태가 집계 밖으로 나간다.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Literal

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

__all__ = [
    "METRIC_PREFIX",
    "AnalysisResult",
    "AnalysisStage",
    "EmbeddingResult",
    "FaceModelLabel",
    "IdentificationObservationResult",
    "IdentificationRequestResult",
    "install_session_gauge",
    "observe_analysis_stage",
    "record_analysis_request",
    "record_embedding_request",
    "record_identification_observations",
    "record_identification_request",
    "render_metrics",
]

METRIC_PREFIX = "classroom_monitoring_"

AnalysisStage = Literal["detect", "pose", "quality", "total"]
"""프레임 한 장을 분석하는 동안 나누어 재는 구간.

- `detect` — SCRFD 얼굴 검출
- `pose` — MediaPipe 랜드마크와 머리 자세
- `quality` — blur·밝기·지문 계산
- `total` — 요청 처리 전체. 위 셋의 합과 벌어지면 나머지(디코딩·직렬화)가 문제다

**`total`은 실제로 분석을 수행한 요청만 남긴다.** 세션 ID가 없거나 이미지를 해석하지
못해 곧바로 거절한 요청까지 섞으면 0에 가까운 값이 분포를 끌어내려, 사용자가 실제로
기다리는 시간을 가린다.
"""

AnalysisResult = Literal["ok", "no_face", "bad_image", "missing_session", "error"]
"""분석 요청이 어떻게 끝났는가.

`no_face`는 실패가 아니라 **정상적인 결과**다. 가이드 안에 얼굴이 없다는 뜻이고
등록 화면은 그것을 보고 안내를 띄운다. `bad_image`·`missing_session`과 섞으면
"사용자가 아직 자세를 못 잡았다"와 "클라이언트가 잘못 보내고 있다"가 구분되지 않는다.
"""

EmbeddingResult = Literal[
    "ok", "bad_image", "not_single_face", "low_confidence", "invalid_vector", "error"
]
"""embedding 생성이 어떻게 끝났는가.

거절 사유를 나누는 이유는 **등록이 왜 실패하는지가 사유마다 다른 조치로 이어지기**
때문이다. `not_single_face`는 촬영 환경(뒤에 사람이 지나감), `low_confidence`는 조명이나
거리, `invalid_vector`는 모델 쪽 문제다. `error`는 예상하지 못한 실패(500)다.
"""

FaceModelLabel = Literal["arcface", "adaface"]
IdentificationRequestResult = Literal[
    "ok",
    "bad_image",
    "invalid_camera",
    "disabled",
    "gallery_unavailable",
    "error",
]
IdentificationObservationResult = Literal["registered", "unknown", "uncertain", "none"]

# 프레임마다 불리는 실시간 경로다. 사람이 "반응이 없다"고 느끼기 시작하는 구간을
# 촘촘히 본다. 1초를 넘어가면 이미 등록 화면이 끊겨 보이므로 그 위는 뭉뚱그린다.
_STAGE_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5)

_ANALYSIS_DURATION_SECONDS = Histogram(
    f"{METRIC_PREFIX}face_analysis_duration_seconds",
    "얼굴 분석 구간별 소요 시간",
    labelnames=("stage",),
    buckets=_STAGE_BUCKETS,
)

_ANALYSIS_REQUESTS_TOTAL = Counter(
    f"{METRIC_PREFIX}face_analysis_requests_total",
    "얼굴 분석 요청 수",
    labelnames=("result",),
)

# embedding은 등록 한 번에 한 번만 불린다. 검출 + 정렬 + 인식을 모두 포함해
# 분석 한 프레임보다 느리므로 버킷 상한을 더 위로 잡는다.
_EMBEDDING_DURATION_SECONDS = Histogram(
    f"{METRIC_PREFIX}face_embedding_duration_seconds",
    "얼굴 embedding 생성에 걸린 시간",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0),
)

_EMBEDDING_REQUESTS_TOTAL = Counter(
    f"{METRIC_PREFIX}face_embedding_requests_total",
    "얼굴 embedding 생성 요청 수",
    labelnames=("result",),
)

_IDENTIFICATION_DURATION_SECONDS = Histogram(
    f"{METRIC_PREFIX}face_identification_duration_seconds",
    "얼굴 식별 요청에 걸린 시간",
    labelnames=("model",),
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0),
)

_IDENTIFICATION_REQUESTS_TOTAL = Counter(
    f"{METRIC_PREFIX}face_identification_requests_total",
    "얼굴 식별 요청 수",
    labelnames=("model", "result"),
)

_IDENTIFICATION_OBSERVATIONS_TOTAL = Counter(
    f"{METRIC_PREFIX}face_identification_observations_total",
    "얼굴 식별 관측 결과 수",
    labelnames=("model", "result"),
)

_SESSIONS_ACTIVE = Gauge(
    f"{METRIC_PREFIX}face_analysis_sessions_active",
    "메모리에 남아 있는 얼굴 등록 세션 수. 계속 늘면 세션이 정리되지 않는 것이다",
)


def observe_analysis_stage(stage: AnalysisStage, started_at: float) -> None:
    """구간 하나의 소요 시간을 남긴다. `started_at`은 `time.perf_counter()` 값이다."""
    _ANALYSIS_DURATION_SECONDS.labels(stage=stage).observe(
        time.perf_counter() - started_at
    )


def record_analysis_request(result: AnalysisResult) -> None:
    _ANALYSIS_REQUESTS_TOTAL.labels(result=result).inc()


def record_embedding_request(
    result: EmbeddingResult, started_at: float | None = None
) -> None:
    """embedding 요청 하나를 남긴다.

    `started_at`이 있으면 소요 시간도 함께 남긴다. 요청 본문을 읽기 전에 실패한
    경우에는 잴 시간이 없으므로 생략할 수 있게 뒀다.
    """
    _EMBEDDING_REQUESTS_TOTAL.labels(result=result).inc()
    if started_at is not None:
        _EMBEDDING_DURATION_SECONDS.observe(time.perf_counter() - started_at)


def record_identification_request(
    model: FaceModelLabel,
    result: IdentificationRequestResult,
    started_at: float,
) -> None:
    """개인 식별자 없이 모델·종료 사유와 지연만 남긴다."""

    _IDENTIFICATION_REQUESTS_TOTAL.labels(model=model, result=result).inc()
    _IDENTIFICATION_DURATION_SECONDS.labels(model=model).observe(
        time.perf_counter() - started_at
    )


def record_identification_observations(
    model: FaceModelLabel,
    results: list[IdentificationObservationResult],
) -> None:
    """학생·카메라 ID를 label에 넣지 않고 관측 상태만 센다."""

    if not results:
        results = ["none"]
    for result in results:
        _IDENTIFICATION_OBSERVATIONS_TOTAL.labels(model=model, result=result).inc()


def install_session_gauge(count: Callable[[], int]) -> None:
    """세션 수를 **스크랩 시점에** 읽도록 연결한다.

    세션이 생기고 사라질 때마다 Gauge를 건드리지 않는 이유는, 그 자리가 요청마다
    잠금을 잡는 경로라서다. 값의 정본은 딕셔너리 하나이므로 필요할 때 세면 된다.
    """
    _SESSIONS_ACTIVE.set_function(lambda: float(count()))


def render_metrics() -> tuple[bytes, str]:
    """쌓인 지표를 Prometheus 텍스트 형식으로 만든다. 본문과 content type을 함께 준다."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
