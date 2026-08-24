# pipeline

**워커가 아니다.** `stream`과 `inference`를 한 프로세스에서 잇는 조립 진입점이다.

## 무엇을 하는가

```text
[pipeline 프로세스]

  camera-01 스레드 ─┐
  camera-02 스레드 ─┼─put─▶ FrameBuffer ─get_latest─▶ inference-consumer 스레드
  camera-03 스레드 ─┘        (오래된 것 버림)              │
                                                          ▼
                                                      학습 YOLO
                                                          │
                                                          ▼
                                              카메라별 ByteTrack
                                                          │
                              지정 입구 카메라만           ▼
                              deeplearning HTTP ◀── 사람 bbox + JPEG
                                      │             (SCRFD · ArcFace · 갤러리)
                                      └──────────▶ student_id 보강
                                                          │
                                      CCTV 문 영역·시각으로 track 인계
                                                          │
                                                          ▼
                                            FastAPI HTTP 또는 탐지 결과 로그
```

설정을 읽고 객체를 조립하는 코드를 여기 한 곳에 모은다. 워커 안에서 서로를
직접 조립하면 나중에 추론을 별도 프로세스로 뗄 때 고칠 곳이 흩어진다.
`app/shared/dependencies.py`가 fastapi에서 하는 일과 같다.

**stream과 inference는 서로를 import하지 않는다.** 둘 다 `shared`의 버퍼만 안다.

## 실행 방법

```bash
cd worker
python -m pip install -r pipeline/requirements.txt
cp pipeline/.env.example pipeline/.env.local    # STREAM_SOURCES를 채운다
export APP_ENV=local   # 생략하면 어차피 local로 동작한다
python -m pipeline.main
```

조립 실행은 워커별 `.env.*`가 아니라 **`pipeline/.env.{APP_ENV}` 하나만** 읽는다.
같은 변수(환경 의존 설정·비밀값)가 여러 파일에 흩어져 어느 값이 적용됐는지 모르게
되는 것을 막는다. 워커를 따로 실행할 때는 각 워커의 `.env.*`를 쓴다.
환경과 무관한 일반 설정(`config/settings.yml`)은 조립 실행에서도 각 워커 자신의
`config/settings.yml`을 그대로 읽는다 — 어차피 같은 값이라 합칠 필요가 없다.

종료는 `Ctrl+C`다. `SIGINT`·`SIGTERM`을 받으면 버퍼를 닫아 추론 소비자를 깨우고,
카메라 스레드를 정리한 뒤 프레임 처리량을 로그로 남긴다.

> **직접 확인한 것**: 필수 환경변수가 없을 때 종료 코드 1로 멈추는 것,
> ultralytics가 없을 때 무엇을 설치해야 하는지 알리고 멈추는 것,
> 수신 → 샘플링 → 버퍼 → 소비자까지 실제 컴포넌트로 잇는 통합 테스트.
> 얼굴 식별 보강·대상 카메라 제한·장애 시 원본 탐지 통과, 카메라별 ByteTrack,
> 역순 프레임을 포함한 입구→CCTV 인계와 좌석까지 신원 유지는 대역으로 검증했다.
> **확인하지 못한 것**: 실제 학습 YOLO·얼굴 인식 가중치를 함께 띄운 end-to-end 동작.
> 장비와 모델이 있는 사람이 확인한 뒤 이 문단을 갱신한다.

## 환경변수와 설정

`.env.{local,dev,prod}` 전체 목록은 [`.env.example`](./.env.example)에 있다 — stream·
inference의 환경 의존 설정·비밀값이며, pipeline 자신의 필드는 없다. 수신·추론 값은
[stream README](../stream/README.md#환경변수와-설정)와
[inference README](../inference/README.md#환경변수와-설정)가 기준이다.

pipeline 자신의 값은 전부 환경과 무관해 [`config/settings.yml`](./config/settings.yml)에
있다.

| 이름 | 용도 | 비고 |
| --- | --- | --- |
| `frame_buffer_maxsize` | 카메라별 최신 프레임 슬롯의 최소 수 | pipeline이 `STREAM_SOURCES` 수 이상으로 자동 확장 |
| `inference_poll_timeout_seconds` | 소비자가 종료 신호를 확인하는 주기 | 기본 0.5 |
| `inference_max_consecutive_failures` | 연속 추론 실패 허용 횟수 | 기본 5 |
| `FACE_IDENTITY_URL` | deeplearning 내부 서비스 주소 | `.env.{APP_ENV}`. 비우면 얼굴 식별 비활성 |
| `FACE_IDENTITY_CAMERA_IDS` | 얼굴 식별할 입구 camera ID 목록 | `.env.{APP_ENV}`. URL을 주면 필수 |
| `INFERENCE_TARGET_CLASS_IDS` | 모델 클래스 번호→이름 JSON | 학습 모델과 함께 설정. 사람 전용 모델은 보통 `{"0":"person"}` |
| `PERSON_TRACKING_CAMERA_IDS` | ByteTrack 대상 camera ID | 비우면 모든 `STREAM_SOURCES` |
| `IDENTITY_HANDOVER_ROUTES` | 입구·CCTV camera ID와 CCTV 문 영역 JSON | FastAPI 설정을 처음 읽기 전과 장애 시 사용할 정적 초기·fallback 값 |
| `bytetrack_*` | 두 단계 매칭·track buffer 기준 | `config/settings.yml` |
| `identity_handover_*` | 인계 시간 창·clock skew·stale·신뢰도와 동적 설정 on/off·갱신 주기·timeout | `config/settings.yml` |
| `face_identity_timeout_seconds` | 얼굴 식별 HTTP timeout | 기본 5초 |
| `face_identity_jpeg_quality` | 얼굴 식별 요청 JPEG 품질 | 기본 95 |
| `face_identity_min_person_confidence` | 얼굴 서비스로 보낼 사람 bbox 최소 신뢰도 | 기본 0.5. ByteTrack 저신뢰도 bbox는 추적에만 사용 |
| `metrics_enabled` | 지표 노출 여부 | 기본 `true` |
| `metrics_host` | 지표 서버 바인딩 주소 | 기본 `0.0.0.0` |
| `metrics_port` | 지표 서버 포트 | 기본 9101 |

### CCTV 문 영역 보정

카메라 간 인계를 켜기 전에 FastAPI의 `/identity-handover` 화면에서 강의실·입구 카메라·
CCTV를 고른다. **CCTV 현재 화면 캡처**를 누르면 저장된 영역이 실제 프레임 위에 겹쳐
보인다. 문 바닥 경계와 다르면 **영역 다시 그리기**로 좁게 다시 그려 저장한다. worker는
기본 5초마다 이 값을 읽어 재시작 없이 반영한다. 조회가 잠시 실패하면 마지막 정상 설정을
유지하고, 화면에서 route를 삭제하면 다음 정상 갱신부터 새 인계를 중단한다.

웹 화면을 쓸 수 없는 개발 환경에서는 아래 CLI로 같은 정규화 좌표를 만들 수 있다.

```bash
cd worker
python -m pipeline.handover_calibration \
  pipeline/data/cctv-handover-reference.jpg \
  --entry-camera-id entry-camera \
  --classroom-camera-id classroom-cctv \
  --preview-output pipeline/data/cctv-entry-zone-preview.jpg
```

마우스로 문 영역을 드래그하고 Enter를 누르면 정규화된
`IDENTITY_HANDOVER_ROUTES=...` 한 줄을 출력한다. 동적 설정이 꺼진 환경이나 장애 fallback이
필요한 배포에서는 그 값을 `pipeline/.env.dev` 또는 `.docker/env/worker.dev.env`에 옮긴다.
GUI를 쓸 수 없는 호스트에서는 `--rect X Y WIDTH HEIGHT`로 같은 픽셀 사각형을 줄 수 있다.

한 사람 bbox의 **하단 중앙점**이 이 영역에 처음 들어오는 순간이 CCTV 인계 후보가 된다.
track이 화면 다른 곳에서 먼저 만들어졌더라도 이후 이 영역으로 들어오면 후보가 된다.
통로 전체나 좌석까지 넓게 잡으면 여러 track이 동시에 후보가 되어 보수적 인계가
의도대로 `UNKNOWN`을 반환하므로, 실제 출입문 바닥 경계만 포함한다.

### 카메라별 최신 한 장만 두는 이유

실시간 파이프라인에서 필요한 것은 **각 카메라의 지금 화면**이다. pipeline은 설정된
카메라 수만큼 슬롯을 자동 확보하고 카메라마다 최신 한 장만 보존한다. 같은 카메라의
새 프레임은 그 카메라의 대기 프레임만 교체하므로 빠른 CCTV가 입구 프레임을 덮지 않는다.
대기 카메라는 공정한 순서로 한 장씩 소비한다.

버린 프레임 수는 종료 시 로그에 남는다. `dropped`가 계속 늘면 추론이 수신을
못 따라가고 있다는 뜻이므로, 버퍼를 키울 게 아니라 `FRAME_SAMPLE_INTERVAL_FRAMES`를
늘리거나 추론을 GPU로 옮기는 것이 맞다.

## 지표 노출

워커는 웹 서버가 아니라서 Prometheus가 긁어갈 곳이 없다. `metrics_enabled`가 켜져
있으면(기본) 조립 진입점이 `metrics_port`에 `/metrics`만 여는 최소 HTTP 서버를
데몬 스레드로 띄운다.

```bash
curl http://127.0.0.1:9101/metrics | grep classroom_monitoring_
```

**서버를 열지 못해도 파이프라인은 그대로 시작한다.** 포트가 이미 쓰이고 있다고 해서
영상 수신과 추론이 멈출 이유가 없다. 관측 수단이 없어진 것이지 기능이 고장 난 것이
아니라서, 오류 로그만 남기고 계속 돈다.

무엇을 왜 재는지와 PromQL 예시는
[`monitoring/internal/README.md`](../../monitoring/internal/README.md#지금-노출하는-지표)가
정본이다. 여기서 반복하지 않는다.

**바인딩 주소 기본값이 `0.0.0.0`인 이유**는 컨테이너 밖(다른 컨테이너의 Prometheus)에서
붙어야 하기 때문이다. 세 환경 모두 컨테이너로 돌아 값이 같아서 `config/settings.yml`에
둔다. 호스트에서 직접 돌리며 사설망에 열고 싶지 않으면 환경변수로 낮춘다
(`METRICS_HOST=127.0.0.1`) — 환경변수가 yml보다 우선한다. 앱 전체에 인증이 없는 상태([결정 0010](../../docs/architecture/decisions.md))라
공인 IP에 그대로 여는 것은 접근 통제 결정 전까지 피한다.

> **직접 확인한 것**: 서버 기동과 `/metrics` 응답을 대역 모델로 실측했고, ByteTrack과
> 신원 인계 지표의 계측 경로를 단위·통합 테스트로 검증했다.
> **확인하지 못한 것**: docker 스택의 Prometheus가 `inference-worker:9101`을 실제로
> 수집하는 것. `--profile worker`로 컨테이너를 띄울 수 있는 환경에서 확인한 뒤
> 이 문단을 갱신한다.

## 실패했을 때

| 상황 | 동작 |
| --- | --- |
| 필수 환경변수 없음 | 시작 시점에 변수 이름을 알리고 종료 코드 1 |
| ultralytics 미설치·가중치 없음 | 무엇을 설치할지 알리고 종료 코드 1 |
| 카메라 한 대 연결 실패 | 그 카메라만 재연결을 반복한다. 다른 카메라는 계속 돈다 |
| 추론 1회 실패 | 스택을 로그로 남기고 다음 프레임으로 넘어간다 |
| 얼굴 식별 HTTP·응답 실패 | 경고를 남기고 신원 없는 원래 사람 탐지를 FastAPI로 보낸다 |
| YOLO 임계값이 ByteTrack high 이상 | 저신뢰도 2단계 매칭이 사라지므로 시작 시 종료 |
| 얼굴 식별 camera ID가 STREAM_SOURCES에 없음 | 호출이 영원히 0건인 상태를 막기 위해 시작 시 종료 |
| 인계 후보 학생 또는 문 영역 신규 track이 여러 명 | 신원을 붙이지 않고 각 CCTV track을 미식별로 둔다 |
| ByteTrack이 buffer보다 오래 끊김 | 이전 신원을 버리고 새 track ID로 시작한다 |
| 추론 연속 실패가 한계 초과 | 파이프라인 전체를 멈춘다. 프레임만 버리며 도는 상태를 두지 않는다 |

## 한 프로세스로 두는 것은 잠정 선택이다

결정 0005는 워커를 단계별로 나눈 이유로 "추론은 GPU에, 수신은 네트워크에 묶이므로
자원을 따로 준다"를 들었다. 한 프로세스에서 돌리면 그 분리를 당분간 못 한다.
지금은 파이프라인을 먼저 잇는 것을 택했고, 배경과 다음 단계는
[결정 0006](../../docs/architecture/decisions.md#0006--워커-사이-프레임-전달을-최신-우선-버퍼로-한다)에 있다.

얼굴 탐지와 얼굴 인식이 붙으면 추론이 지금보다 무거워진다. 그때 프로세스 분리를
다시 판단한다.

## 테스트

```bash
cd worker
python -m pytest pipeline/tests -q
```

- `test_runner.py` — 수신·추론의 수명 관리와 종료 처리
- `test_end_to_end.py` — 실제 컴포넌트로 수신부터 추론 호출까지 잇는 통합 검증.
  대역은 OpenCV VideoCapture와 모델 호출 두 곳뿐이다

## 관련 문서

- [worker 개요](../README.md)
- [shared — 프레임 버퍼](../shared/README.md)
- [stream worker](../stream/README.md) · [inference worker](../inference/README.md)
