# inference worker

`stream` worker가 고른 프레임을 꺼내 모델을 호출하는 **실행 단계**다.

## 이 워커는 모델을 소유하지 않는다

[결정 0009](../../docs/architecture/decisions.md#0009--추론-책임을-모델과-실행으로-나눈다)가
추론 책임을 둘로 나눴다.

| 담당 | 무엇을 아는가 |
| --- | --- |
| [`deeplearning`](../../deeplearning/README.md) | 모델 종류, 가중치, 전처리, 후처리, 탐지 결과 스키마 |
| `worker/inference` | 언제 호출하는가, 실패하면 어떻게 하는가, 언제 멈추는가 |

얼굴 식별은 이 경계를 만족한다. 이 워커는 모델·갤러리를 모르고 deeplearning의 내부
HTTP 계약만 호출한다. 사람 탐지는 아직 `model.py`가 ultralytics를 직접 부르는 잠정
상태다. 새 모델 코드는 여기가 아니라 `deeplearning`에 만든다.

## 현재 상태

`MODEL_PATH`로 지정한 YOLO 가중치로 프레임 한 장을 탐지한다. 사용할 클래스 번호와
이름은 `INFERENCE_TARGET_CLASS_IDS`로 함께 지정한다. 기본 COCO 호환 설정은 `person`과
`cell phone`이고, 프로젝트의 사람 전용 학습 모델은 `{"0":"person"}`을 사용한다.
입력은 두 갈래다.

- **프레임 버퍼** — `stream`이 넣은 최신 프레임을 소비자 루프가 꺼내 쓴다.
  실행은 [`pipeline`](../pipeline/README.md) 진입점이다.
- **이미지 파일** — `python -m inference.main <이미지경로>`로 한 장만 확인한다.

**`cell phone` 클래스는 이전 주제(직원 통화 판정)의 하위 호환 기본값이며 강의실 학생
모니터링에서는 쓰지 않는다.** 사람 전용 학습 가중치를 쓸 때는 클래스 설정에서도 뺀다.

`pipeline`은 사람 탐지에 카메라별 ByteTrack을 먼저 붙인다. `FACE_IDENTITY_URL`과 입구
카메라 ID를 설정하면 해당 프레임만 deeplearning에 보내 `student_id`·식별 신뢰도·얼굴
bbox를 사람 track에 보강한다. 인계 route가 있으면 CCTV 사람 track의 하단 중앙점이 문
영역에 처음 들어오는 순간 그 신원을 넘겨 같은 track이 좌석까지 이동하는 동안 유지한다.
track이 문 영역 밖에서 먼저 만들어진 경우도 이후 경계 진입을 감지한다.
입구 detector confidence 변화로 같은 사람이 `face-*` fallback과 `person-*` track으로
달라져도, 활성 학생 한 명은 CCTV track 하나에만 인계한다.
얼굴 서비스가 실패하면 원래 사람 탐지를 그대로 FastAPI에 보내 좌석 점유 경로를
멈추지 않는다. `FASTAPI_URL`을 설정하면 최종 결과를 `/internal/inference/events`로
전송하며, 설정하지 않으면 로그만 출력한다. track 생성·만료·활성 수와 수명, 인계 결과
지표는 구현됐고 현장 기준선과 Grafana 패널은 아직 남아 있다.

## 서비스 목적

프레임을 받아 "무엇이 어디에 있는가"를 좌표와 신뢰도로 돌려준다.
이 워커가 없으면 영상은 흘러갈 뿐 아무 정보도 만들어내지 못한다.

## 구성

| 파일 | 역할 |
| --- | --- |
| `config.py` | 모델 경로·장치·임계값 설정 |
| `types.py` | `Detection`, `InferenceResult` — 탐지 결과 형식 |
| `model.py` | `Yolo8nDetector` — 모델 로딩과 탐지 |
| `processor.py` | 프레임을 모델에 넘기는 경계. 추론 지연·탐지 신뢰도를 재는 자리 |
| `consumer.py` | 프레임 버퍼에서 최신 프레임을 꺼내 도는 소비자 루프 |
| `handler.py` | 결과를 FastAPI 내부 API 계약으로 직렬화하고 제한 재시도 |
| `face_identity.py` | 입구 프레임을 deeplearning에 보내 얼굴 식별 결과를 사람 탐지에 보강. 실패하면 원본 탐지를 통과시킴 |
| `tracking.py` | 사람 bbox를 카메라별 ByteTrack 두 단계 매칭으로 이어 `person-<번호>` 부여 |
| `identity_handover.py` | FastAPI route를 주기적으로 읽고 입구 신원을 CCTV 문 영역에 진입한 유일한 track에 인계해 track 수명 동안 유지 |
| `metrics.py` | 추론·ByteTrack·신원 인계 Prometheus 지표 정의 |
| `fixtures/` | 얼굴·영상 없는 모델 연동 계약 fixture |
| `main.py` | 이미지 파일 한 장을 검사하는 진입점 |

**`stream`을 import하지 않는다.** 소비자는 [`shared`의 프레임 버퍼](../shared/README.md)만
알고, 누가 프레임을 넣는지 모른다.

## 탐지 결과 형식

```python
InferenceResult(
    frame_shape=(480, 640, 3),
    detections=(
        Detection(
            class_id=0,
            class_name="person",
            confidence=0.87,
            bbox=(12, 30, 220, 470),
            student_id="student-uuid",           # 선택
            identity_confidence=0.88,             # 선택
            face_bbox=(40, 50, 120, 150),         # 선택
            track_id="person-12",                  # 선택
        ),
    ),
)
```

`bbox`와 `face_bbox`는 원본 프레임 기준 `(x1, y1, x2, y2)` 픽셀 좌표다. 식별 성공이면
`student_id`와 `identity_confidence`를 함께 채운다. 사람 ByteTrack ID가 이미 있으면 얼굴
track ID로 덮어쓰지 않는다. 미식별 사람도 `track_id`는 남길 수 있지만 신원 필드는
비운다. 불완전한 조합은 HTTP payload에서 안전하게 미식별로 낮춘다.
전체 내부 API 계약과 검증 방법은 [모델 연동 인계](./MODEL_INTEGRATION.md)를 따른다.

**여기까지가 이 워커의 출력이다.** `PRESENT`, `WRONG_SEAT`, `ABSENT` 같은 업무
의미는 붙이지 않는다. 그 해석은 `webapps/fastapi`의 일이다
([결정 0008](../../docs/architecture/decisions.md#0008--학생-상태-판정을-rule-engine으로-분리하고-fastapi가-소유한다)).

**식별하지 못한 것을 억지로 식별하지 않는다.** 신뢰도가 기준 미만이면 신원 필드를
비우고 가장 가까운 학생을 고르지 않는다. `UNKNOWN` 판정은 FastAPI가 한다. 오인식은
다른 학생의 정보를 노출하는 사고다.

## 포함하지 않아야 할 기능

- 스트림 연결 관리와 재연결 → [`stream`](../stream/README.md) 책임
- 모델 종류·가중치·전처리 → [`deeplearning`](../../deeplearning/README.md) 책임
- 탐지 결과의 업무 해석 → `webapps/fastapi` 책임
- 탐지 결과의 영속 저장 → `webapps/fastapi` 책임
- 영상 저장 → [`recorder`](../recorder/README.md) 책임

## 기술

| 항목 | 상태 | 비고 |
| --- | --- | --- |
| 언어 | Python | 3.12 이상 |
| 사람 탐지 모델 | YOLOv8n (ultralytics) | 버전 고정은 `결정 필요`. 소유는 `deeplearning`으로 이관 `예정` |
| 얼굴 탐지 모델 | SCRFD | deeplearning 내부 HTTP로 호출. 최종 버전은 결정 필요 |
| 얼굴 인식 모델 | ArcFace(현재), AdaFace R50(평가 가능) | 비교와 임계값 선택은 `deeplearning` 책임 |
| 실행 장치 | CPU 기본, CUDA 선택 | `INFERENCE_DEVICE`로 고른다 |
| 프레임 수신 | 프레임 버퍼 | [결정 0006](../../docs/architecture/decisions.md#0006--워커-사이-프레임-전달을-최신-우선-버퍼로-한다) |
| 결과 전달 방식 | HTTP | `FASTAPI_URL` 설정 시 내부 API로 제한 재시도, 미설정 시 로그 출력 |

**모델 가중치 파일은 저장소에 커밋하지 않는다.** 경로는 `MODEL_PATH`로 주입한다.
파일이 없으면 ultralytics가 이름을 보고 내려받는다.

**모델은 프로세스 시작 시 한 번만 로딩한다.** 프레임마다 불러오면 추론이 멈춘다.

## 실행 방법

```bash
cd worker
python -m pip install -r inference/requirements.txt
cp inference/.env.example inference/.env.local
export APP_ENV=local   # 생략하면 어차피 local로 동작한다

# 이미지 한 장 검사
python -m inference.main path/to/image.jpg

# stream과 이어서 실행
python -m pipeline.main
```

> **직접 확인한 것**: 대역 모델로 탐지 결과 변환과 소비자 루프(최신 프레임 선택,
> 실패 처리, 종료) 동작.
> **실제 GPU에서 확인한 것**(GTX 1060 3GB, 드라이버 560.94): 합성 RTSP 스트림을
> `INFERENCE_DEVICE=cuda`로 받아 YOLOv8n이 사람 4명을 탐지하고(14건 처리, 0 실패)
> 스냅샷이 MinIO에 적재되는 것까지.
> **확인하지 못한 것**: 실제 카메라, 다중 카메라 장시간 안정성.
> **측정하지 않은 것**: FPS, 추론 지연, 정확도. 측정 전에는 수치를 적지 않는다.

> **GPU 경로가 한동안 깨져 있었다.** `model.py`가 모델 출력을 `np.asarray`로 바로
> 변환했는데, CUDA 텐서는 그렇게 변환되지 않아 `INFERENCE_DEVICE=cuda`로 돌리면
> 프레임마다 `TypeError`가 났고 연속 실패 한도에 걸려 파이프라인이 멈췄다.
> 호스트 메모리로 먼저 옮기도록 고쳤다(`_to_numpy`). CPU로만 확인해서는 드러나지
> 않는 종류의 버그다.

## 환경변수와 설정

환경마다 달라야 하는 값·비밀값은 `.env.{local,dev,prod}`([`.env.example`](./.env.example)이
기준)에, 환경과 무관한 일반 설정·판정 기준값은 커밋된
[`config/settings.yml`](./config/settings.yml)에 있다.
조립 실행에서는 `.env.*`는 이 디렉터리가 아니라 `pipeline/.env.{APP_ENV}`를 읽지만,
`config/settings.yml`은 조립 실행에서도 이 디렉터리 것을 그대로 읽는다.

### `.env.{local,dev,prod}`

| 이름 | 용도 | 비고 |
| --- | --- | --- |
| `MODEL_PATH` | 모델 가중치 경로 | 기본 `yolo11m.pt`. GPU 서버는 다른 경로를 쓸 수 있다 |
| `INFERENCE_TARGET_CLASS_IDS` | 탐지할 모델 클래스 JSON | 기본 `{"0":"person","67":"cell phone"}`. 사람 전용 학습 모델은 `{"0":"person"}` |
| `INFERENCE_DEVICE` | 실행 장치 | `cpu` / `cuda`. local은 보통 cpu, dev는 cuda |
| `OBJECT_STORAGE_BACKEND` | 객체 저장소 종류 | `local` / `minio`. local은 보통 `local` |
| `OBJECT_STORAGE_ENDPOINT`, `_ACCESS_KEY`, `_SECRET_KEY` | MinIO 접속 정보 | `minio` backend에서만 필요. 비밀값 |

얼굴 식별 주소와 대상 카메라는 조립 진입점의 `.env.{APP_ENV}`에 둔다.
`FACE_IDENTITY_URL`이 비어 있으면 기존 사람 탐지만 유지한다.
`FACE_IDENTITY_CAMERA_IDS`에는 FastAPI에서 `IDENTITY_ONLY` 역할로 등록한 입구
`camera_id`만 쉼표로 구분해 넣는다. 좌석 CCTV는 넣지 않는다.

### `config/settings.yml`

| 이름 | 용도 | 비고 |
| --- | --- | --- |
| `inference_confidence_threshold` | 탐지 신뢰도 임계값 | 기본 0.25. 좌석 점유로 인정할지는 fastapi가 다시 판단한다 |
| `inference_image_size` | 추론 입력 크기(긴 변) | **기본 1280.** 지정하지 않으면 ultralytics가 640으로 줄여 뒤쪽에 앉은 사람을 놓친다. 실측 근거는 `config/settings.yml` 주석에 있다 |
| `snapshot_enabled` | 탐지 스냅샷 적재 | 기본 `false`. 저장은 명시적으로 켠다 |
| `snapshot_max_long_side_px` | 긴 변 상한 | 기본 1280(720p). 확대하지 않는다 |
| `snapshot_jpeg_quality` | JPEG 품질 | 기본 80 |
| `snapshot_min_interval_seconds` | 카메라당 최소 적재 간격 | 기본 60 |
| `object_storage_bucket`, `_secure`, `_timeout_seconds` | 버킷 이름·TLS·타임아웃 | `snapshot_enabled: true`일 때만 필요 |

소비자 루프의 대기 시간과 연속 실패 허용치는 조립 쪽 설정이다.
[pipeline README](../pipeline/README.md#환경변수와-설정)를 따른다.

## 탐지 스냅샷

**영상 원본을 저장하지 않는다.** 대신 탐지 시점의 정지 이미지를 객체 저장소에 남긴다
([결정 0028](../../docs/architecture/decisions.md#0028--영상-원본을-저장하지-않고-스냅샷만-남긴다)).
공용 GPU 서버의 가용 용량이 약 48 GB인데 1080p 카메라 한 대가 시간당 약 0.9 GB라
상시 녹화가 성립하지 않는다.

프레임을 이미 들고 있는 쪽이 이 워커라서 여기서 만든다. `recorder`처럼 RTSP를 따로
받지 않으므로 추가 디코딩이 없다.

**적재 조건은 둘 다 만족해야 한다.**

1. 탐지 **개수**가 직전에 *올린* 개수와 다르다
2. 그 카메라의 마지막 **시도**에서 `SNAPSHOT_MIN_INTERVAL_SECONDS`가 지났다

1번이 "마지막으로 본" 개수가 아니라 "마지막으로 올린" 개수인 것이 중요하다. 간격 캡에
막혀 건너뛴 변화가 다음 기회에 그대로 올라간다. 아직 아무것도 올리지 않았으면 0을
기준으로 보므로, 기동 직후 아무도 없는 화면을 한 장 올리는 일이 없다.

**2번이 없으면 용량 계산이 무너진다.** 탐지가 경계에서 떨릴 때(2명 ↔ 3명 반복)
적재가 폭주한다. 상한 없는 적재 경로를 만들지 않는다.

**업무 의미를 붙이지 않는다.** 여기서 보는 것은 개수 변화까지다. "학생이 자리를
비웠다" 같은 해석은 `fastapi`가 한다(결정 0008·0009).

2번이 "마지막 적재"가 아니라 "마지막 시도"인 것도 이유가 있다. 실패한 시도도 시간을
쓴다. 성공만 시각으로 남기면 한 번도 성공하지 못한 카메라는 간격 캡이 영영 걸리지
않아, 개수가 바뀐 프레임마다 저장소를 다시 두드린다. MinIO가 내려가 있으면 그때마다
접속 timeout(5초)을 기다리게 되고 그동안 추론 소비자 스레드가 멈춘다.

적재에 실패하면 로그만 남기고 넘어간다. **개수**는 갱신하지 않으므로 놓친 변화는
다음 간격이 지나면 그대로 올라간다 — 저장소가 살아나면 저절로 이어진다.
**저장소 장애가 탐지를 멈추지 않는다.**

### 저장소가 아예 없을 때

`SNAPSHOT_ENABLED=true`인데 기동 시점에 저장소를 준비하지 못하면(MinIO 미기동,
버킷 확인 실패) **스냅샷만 끄고 파이프라인은 그대로 시작한다.** 이 워커의 본업은
탐지 결과를 `fastapi`로 넘기는 것이고 스냅샷은 부가 기능이다. 있어도 그만인 기능
하나가 반드시 돌아야 하는 기능 전체를 멈추게 두지 않는다.

낮춘 상태는 **그 실행 내내 유지된다.** MinIO가 나중에 올라와도 스냅샷은 다시
켜지지 않으므로 워커를 재시작해야 한다. 시작할 때 `WARNING`으로 남는다.

`recorder`는 반대로 즉시 종료한다. 그쪽은 저장이 본업이라 저장소 없이 도는 것에
의미가 없다.

객체 키는 `<카메라>/<날짜>/<시각>.jpg`이며 규칙은
[`shared/object_keys.py`](../shared/object_keys.py)에 있다. 보존 기간 삭제는 이 워커가
하지 않는다 — 저장소의 lifecycle 규칙이 한다.

## 실패했을 때

- **추론 1회 실패** — 스택을 로그로 남기고 다음 프레임으로 넘어간다. 프레임 한 장
  때문에 파이프라인 전체를 죽이지 않는다.
- **연속 실패가 한계를 넘음** — 종료 신호를 켜서 수신까지 함께 멈춘다. 계속 실패하는
  상태로 도는 것은 프레임만 버리면서 아무것도 만들지 않는 것과 같다.
- **객체 저장소를 준비하지 못함** — 스냅샷만 끄고 계속한다. 위
  [저장소가 아예 없을 때](#저장소가-아예-없을-때)를 따른다.

## 노출하는 지표

정의는 [`metrics.py`](./metrics.py) 한곳에 모으고, 모델·루프·추적·얼굴 호출·인계
경계에서 계측한다. 노출 경로와 포트는
[조립 진입점](../pipeline/README.md#지표-노출)이 연다.

| 지표 | 타입 | label |
| --- | --- | --- |
| `classroom_monitoring_inference_duration_seconds` | Histogram | 없음 |
| `classroom_monitoring_frames_processed_total` | Counter | `camera_id`, `result` |
| `classroom_monitoring_inference_consecutive_failures` | Gauge | 없음 |
| `classroom_monitoring_detections_total` | Counter | `class_name` |
| `classroom_monitoring_detection_confidence` | Histogram | `class_name` |
| `classroom_monitoring_face_identification_requests_total` | Counter | `outcome` (`ok`, `error`) |
| `classroom_monitoring_face_identification_duration_seconds` | Histogram | 없음 |

**실패한 추론의 시간은 지연 분포에 넣지 않는다.** 즉시 터진 호출이 "아주 빠른 추론"
으로 섞이면 분포가 거짓말을 한다. 실패는 `frames_processed_total{result="failed"}`가
따로 센다.

**프레임 번호·이벤트 id·학생 id를 label로 쓰지 않는다.** 값이 무한히 늘어나고,
학생 id는 개인을 식별하는 값이라 접근 통제가 약한 `/metrics`로 나가서는 안 된다.
무엇을 왜 재는지와 PromQL 예시는
[`monitoring/internal/README.md`](../../monitoring/internal/README.md#지금-노출하는-지표)가
정본이다.

## 테스트 전략

기본 테스트는 실제 가중치와 GPU 없이 돈다. 모델 호출을 대역으로 바꾼다.

```bash
cd worker
python -m pytest inference/tests -q
```

- `test_model.py` — 대역 모델로 탐지 결과 변환 검증
- `test_consumer.py` — 최신 프레임 선택, 실패 누적과 중단, 종료 처리
- `test_handler.py` — 승인 fixture와 payload 일치, 멱등 ID, HTTP 제한 재시도 검증
- `test_face_identity.py` — 식별 결과 보강, 미식별 track, 응답 검증, 대상 카메라 제한과 장애 시 원본 탐지 통과
- `test_tracking.py` — ByteTrack 고·저신뢰도 2단계 매칭, 짧은 미탐 회복, 만료, 카메라 격리
- `test_identity_handover.py` — 입구→CCTV 인계, 좌석까지 유지, 역순 프레임, 다중 후보 거부
- `test_metrics.py` — 지연·탐지 신뢰도·처리 결과 계측. 실패한 추론이 지연 분포에
  들어가지 않는 것과 연속 실패 Gauge가 성공 시 0으로 돌아가는 것을 함께 본다

실제 가중치가 필요한 확인은 기본 실행에 넣지 않는다.
측정하지 않은 성능 수치를 문서나 보고에 적지 않는다.
**테스트 자산에 실제 사람의 얼굴을 쓰지 않는다.**

## 관련 문서

- [worker 개요](../README.md)
- [모델 연동 인계](./MODEL_INTEGRATION.md) — 내부 이벤트 필드·오류·fixture 검증
- [deeplearning](../../deeplearning/README.md) — 모델 책임 범위
- [조립 진입점](../pipeline/README.md) · [shared 프레임 버퍼](../shared/README.md)
- [AI 에이전트 규칙](../../docs/agents/ai-agent.md)
- [아키텍처](../../docs/architecture/README.md)
