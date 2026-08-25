"""추론 워커가 노출하는 Prometheus 지표 정의.

**정의를 여기 한곳에 모은다.** 계측 코드가 로직에 흩어지면 어떤 지표가 어디서
올라오는지 추적할 수 없고, label을 바꿀 때 고칠 곳도 흩어진다. 실제 계측은
`processor.py`(모델 호출), `consumer.py`(루프), `tracking.py`(track 수명),
`face_identity.py`(얼굴 서비스 호출), `identity_handover.py`(인계 결과)에 있다.

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
| `face_identification_requests_total` | `outcome` | 2 (`ok`, `error`) |
| `face_identification_duration_seconds` | 없음 | 1 |
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
    "FACE_IDENTIFICATION_DURATION_SECONDS",
    "FACE_IDENTIFICATION_REQUESTS_TOTAL",
    "IDENTITY_HANDOFF_TOTAL",
    "INFERENCE_DURATION_SECONDS",
    "PERSON_TRACKS_ACTIVE",
    "PERSON_TRACKS_CREATED_TOTAL",
    "PERSON_TRACKS_EXPIRED_TOTAL",
    "PERSON_TRACK_LIFETIME_FRAMES",
    "RESULT_DISPATCH_DROPPED_TOTAL",
    "RESULT_DISPATCH_DURATION_SECONDS",
    "RESULT_DISPATCH_FAILED_TOTAL",
    "RESULT_DISPATCH_QUEUE_DEPTH",
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

# 얼굴 서비스 장애는 사람 탐지 흐름을 막지 않는 fail-open이라 컨테이너 상태만 보면
# 놓친다. 실제 HTTP 요청의 성공/실패와 지연을 따로 노출한다. camera/student 식별자는
# label에 넣지 않아 카디널리티와 개인정보 노출을 제한한다.
FACE_IDENTIFICATION_REQUESTS_TOTAL = Counter(
    f"{METRIC_PREFIX}face_identification_requests_total",
    "deeplearning 얼굴 식별 서비스 호출 결과",
    labelnames=("outcome",),
)

FACE_IDENTIFICATION_DURATION_SECONDS = Histogram(
    f"{METRIC_PREFIX}face_identification_duration_seconds",
    "얼굴 식별 서비스 호출과 응답 검증에 걸린 시간",
    buckets=(0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# outcome은 accepted / no_candidate / ambiguous_candidates / ambiguous_tracks 네
# 값뿐이다. 학생 식별자는 개인정보이므로 포함하지 않는다.
IDENTITY_HANDOFF_TOTAL = Counter(
    f"{METRIC_PREFIX}identity_handoff_total",
    "입구 신원을 교실 CCTV track으로 인계한 시도 결과",
    labelnames=("outcome",),
)


# --- 결과 전송 분리 ---
# **여기가 지금까지 보이지 않던 구간이다.** 추론은 16ms인데 소비자 한 장의 주기가
# 800ms였고, 그 차이는 FastAPI 왕복(내부적으로 MongoDB Atlas 왕복 여러 번)이었다.
# 재는 자리가 없어서 추정으로만 알 수 있었으므로 지표를 먼저 만든다.
#
# `channel`은 detection / entry 두 값이다. 두 경로의 비용이 다르고(입구는 얼굴
# 서비스 호출이 앞에 붙는다) 막히는 원인도 다르다.
RESULT_DISPATCH_DURATION_SECONDS = Histogram(
    f"{METRIC_PREFIX}result_dispatch_duration_seconds",
    "결과 핸들러(FastAPI 전송 포함) 한 건을 처리하는 데 걸린 시간",
    labelnames=("channel",),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# 전송 큐에 밀린 건수. 0에서 떨어지지 않으면 전송이 생산을 못 따라간다는 뜻이다.
RESULT_DISPATCH_QUEUE_DEPTH = Gauge(
    f"{METRIC_PREFIX}result_dispatch_queue_depth",
    "전송 대기 중인 결과 수",
    labelnames=("channel",),
)

# 큐가 가득 차 버린 결과 수. **프레임 버퍼의 dropped와 뜻이 같다** — 밀린 것을
# 붙들고 있는 것보다 최신을 보내는 편이 실시간 파이프라인에 맞다.
RESULT_DISPATCH_DROPPED_TOTAL = Counter(
    f"{METRIC_PREFIX}result_dispatch_dropped_total",
    "전송 큐가 가득 차 버린 결과 수",
    labelnames=("channel",),
)

# 핸들러가 던진 예외 수. 전송 스레드는 예외를 밖으로 내보내지 않으므로
# 이 값이 오르지 않는지로 확인한다.
RESULT_DISPATCH_FAILED_TOTAL = Counter(
    f"{METRIC_PREFIX}result_dispatch_failed_total",
    "결과 핸들러가 예외로 끝난 횟수",
    labelnames=("channel",),
)
