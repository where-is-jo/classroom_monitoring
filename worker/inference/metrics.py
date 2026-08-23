"""추론 워커가 노출하는 Prometheus 지표 정의.

**정의를 여기 한곳에 모은다.** 계측 코드가 로직에 흩어지면 어떤 지표가 어디서
올라오는지 추적할 수 없고, label을 바꿀 때 고칠 곳도 흩어진다. 실제 계측은
`processor.py`(모델 호출), `consumer.py`(루프), `tracking.py`(track 수명),
`identity_handover.py`(인계 결과)에 있다.

프레임 버퍼 지표는 여기 없다. stream과 함께 쓰는 값이라 `shared/metrics.py`에 있다.

## label 값 종류 수 (고카디널리티 점검)

| 지표 | label | 예상 값 수 |
| --- | --- | --- |
| `frames_processed_total` | `camera_id` × `result` | 카메라 대수 × 2 |
| `detections_total` | `class_name` | 2 (`person`, `cell phone`) |
| `detection_confidence` | `class_name` | 2 |
| `inference_duration_seconds` | 없음 | 1 |
| `inference_consecutive_failures` | 없음 | 1 |
| `person_tracks_*` | `camera_id` | 카메라 대수 |
| `identity_handoff_total` | `outcome` | 4 |

`camera_id`는 `STREAM_SOURCES`에 적은 카메라만 나오므로 대수가 고정이다. **프레임
번호·이벤트 id·학생 id는 label로 쓰지 않는다** — 값이 무한히 늘어나고, 학생 id는
개인을 식별하는 값이라 접근 통제가 약한 `/metrics`로 나가서는 안 된다
(`monitoring/internal/README.md`).
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram
from shared.metrics import METRIC_PREFIX

__all__ = [
    "CONSECUTIVE_FAILURES",
    "DETECTIONS_TOTAL",
    "DETECTION_CONFIDENCE",
    "FRAMES_PROCESSED_TOTAL",
    "IDENTITY_HANDOFF_TOTAL",
    "INFERENCE_DURATION_SECONDS",
    "PERSON_TRACKS_ACTIVE",
    "PERSON_TRACKS_CREATED_TOTAL",
    "PERSON_TRACKS_EXPIRED_TOTAL",
    "PERSON_TRACK_LIFETIME_FRAMES",
]

# 모델 호출 한 번에 걸린 시간. **평균이 아니라 분포로 본다** — 평균은 가끔 튀는
# 느린 프레임을 가리는데, 실시간 파이프라인에서 문제가 되는 것이 바로 그 프레임이다.
#
# 버킷은 두 실행 장치를 한 지표로 덮도록 잡았다. CUDA에서는 수십 ms, CPU에서는
# 수백 ms~수 초가 나온다(INFERENCE_DEVICE). 10초를 넘으면 +Inf로 모인다.
INFERENCE_DURATION_SECONDS = Histogram(
    f"{METRIC_PREFIX}inference_duration_seconds",
    "프레임 한 장을 추론하는 데 걸린 시간",
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# 소비자가 프레임 한 장을 처리한 결과. `result`는 ok / failed 두 값이다.
# 실패율을 보려면 분모가 필요해서 성공도 같은 지표에 담는다.
FRAMES_PROCESSED_TOTAL = Counter(
    f"{METRIC_PREFIX}frames_processed_total",
    "추론 소비자가 처리한 프레임 수",
    labelnames=("camera_id", "result"),
)

# **파이프라인이 스스로 멈추기 전에 알기 위한 지표다.** 연속 실패가
# INFERENCE_MAX_CONSECUTIVE_FAILURES(기본 5)에 닿으면 소비자가 shutdown_event를
# 세워 파이프라인 전체를 멈춘다. 지금까지 그 사실은 로그에만 남아서, 멈춘 뒤에야
# 알 수 있었다. 성공하면 0으로 돌아가므로 Counter가 아니라 Gauge다.
CONSECUTIVE_FAILURES = Gauge(
    f"{METRIC_PREFIX}inference_consecutive_failures",
    "연속으로 실패한 추론 횟수. 한계에 닿으면 파이프라인이 멈춘다",
)

# 탐지 건수. 갑자기 0에 붙으면 모델이 아니라 화면이 바뀐 것일 수 있다
# (카메라 각도 변경, 조명, 렌즈 가림).
DETECTIONS_TOTAL = Counter(
    f"{METRIC_PREFIX}detections_total",
    "모델이 찾아낸 탐지 건수",
    labelnames=("class_name",),
)

# **정답 라벨이 없는 운영 환경에서 모델 품질 저하를 잡는 대리 지표다.** 지연과
# 처리량은 시스템이 도는지만 알려주고 모델이 맞는 답을 내는지는 알려주지 않는다.
# 촬영 환경이 바뀌면 탐지가 끊기기 전에 신뢰도 분포가 먼저 내려간다.
#
# 값의 범위가 0~1이라 기본 버킷(초 단위)을 쓸 수 없다. 하한은 탐지 임계값
# 기본값(INFERENCE_CONFIDENCE_THRESHOLD=0.25) 바로 위에서 시작한다 — 그보다 낮은
# 값은 모델이 애초에 돌려주지 않는다.
DETECTION_CONFIDENCE = Histogram(
    f"{METRIC_PREFIX}detection_confidence",
    "탐지 신뢰도 분포. 내려가면 모델이나 촬영 환경이 바뀐 것이다",
    labelnames=("class_name",),
    buckets=(0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0),
)

# ByteTrack 상태. camera_id는 설정된 카메라 목록 안에서만 나오며, track_id나
# student_id는 label로 내보내지 않는다.
PERSON_TRACKS_CREATED_TOTAL = Counter(
    f"{METRIC_PREFIX}person_tracks_created_total",
    "ByteTrack이 새로 만든 사람 track 수",
    labelnames=("camera_id",),
)

PERSON_TRACKS_EXPIRED_TOTAL = Counter(
    f"{METRIC_PREFIX}person_tracks_expired_total",
    "track buffer를 넘겨 만료된 사람 track 수",
    labelnames=("camera_id",),
)

PERSON_TRACKS_ACTIVE = Gauge(
    f"{METRIC_PREFIX}person_tracks_active",
    "카메라별 활성·유실 대기 중 사람 track 수",
    labelnames=("camera_id",),
)

PERSON_TRACK_LIFETIME_FRAMES = Histogram(
    f"{METRIC_PREFIX}person_track_lifetime_frames",
    "만료된 ByteTrack이 유지된 처리 프레임 수",
    labelnames=("camera_id",),
    buckets=(5, 10, 20, 30, 60, 120, 300, 600, 1800, 3600),
)

# outcome은 accepted / no_candidate / ambiguous_candidates / ambiguous_tracks 네
# 값뿐이다. 학생 식별자는 개인정보이므로 포함하지 않는다.
IDENTITY_HANDOFF_TOTAL = Counter(
    f"{METRIC_PREFIX}identity_handoff_total",
    "입구 신원을 교실 CCTV track으로 인계한 시도 결과",
    labelnames=("outcome",),
)
