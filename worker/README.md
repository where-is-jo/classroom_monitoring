# worker

강의실 카메라 영상을 받아 추론과 저장으로 넘기는 백그라운드 서비스 묶음이다.

> **범위 주의**: 담당은 **영상 파이프라인으로 한정**한다.
> 큐 소비, 배치 작업 같은 일반 백그라운드 작업은 고려 대상이 아니다.
> 그런 작업이 필요해지면 여기에 끼워 넣기 전에 책임 분리를 먼저 검토한다.

## 워커 구성

영상 파이프라인의 단계별로 워커를 나눈다. 단계마다 멈추는 이유와 확장해야 할 자원이
다르기 때문이다. 배경은 [결정 0005](../docs/architecture/decisions.md#0005--worker를-역할별-워커로-분리)에 있다.

```text
[강의실 카메라 / Jetson] ─RTSP─→ [MediaMTX]
                                     │
                                     ▼
                              ① stream worker       영상 수신 · 재연결 · 프레임 샘플링
                                     │
                       ┌─────────────┴─────────────┐
                       ▼                           ▼
                ② inference worker           ③ recorder worker
                   사람 탐지 → 입구 얼굴 식별     영상 세그먼트 → MinIO
                       │
                       ▼
                탐지 결과(student_id · bbox · 신뢰도)
                       │
                       ▼
                webapps/fastapi   ← HTTP 수신·저장·상태 판정
```

| 디렉터리 | 책임 | 상태 |
| --- | --- | --- |
| [`stream`](./stream/README.md) | RTSP 영상 수집, 연결 유지·재연결, 프레임 샘플링 | 동작 |
| [`inference`](./inference/README.md) | 프레임을 꺼내 모델을 호출하는 실행 단계 | 동작. `FASTAPI_URL` 설정 시 결과를 HTTP로 전달 |
| [`recorder`](./recorder/README.md) | 영상 세그먼트를 객체 저장소에 적재 | 동작하나 **공용 서버에서 실행하지 않는다**([결정 0028](../docs/architecture/decisions.md#0028--영상-원본을-저장하지-않고-스냅샷만-남긴다)) |

워커가 아닌 디렉터리가 둘 있다.

| 디렉터리 | 역할 |
| --- | --- |
| [`shared`](./shared/README.md) | 워커들이 함께 쓰는 계약. 프레임 타입과 프레임 버퍼 |
| [`pipeline`](./pipeline/README.md) | `stream`과 `inference`를 한 프로세스로 잇는 조립 진입점 |

**상태 판정 워커를 두지 않는다.** 탐지 결과를 학생 상태(`PRESENT` / `WRONG_SEAT` /
`ABSENT`)로 바꾸는 일은 `webapps/fastapi`가 소유한다
([결정 0008](../docs/architecture/decisions.md#0008--학생-상태-판정을-rule-engine으로-분리하고-fastapi가-소유한다)).
그 대안을 위해 있던 `worker/state` 디렉터리는 이 결정으로 지웠다.

**워커를 카메라 대수만큼 띄우지 않는다.** 워커 애플리케이션 하나가 내부에서 여러
스트림을 스레드로 관리한다. 카메라를 늘리려고 프로세스를 그만큼 띄우면 설정과
장애 지점이 함께 늘어난다.

## 현재 상태

**`stream` → `inference`까지 이어진다.** 카메라에서 받은 프레임을 일정 간격으로
샘플링해 프레임 버퍼에 넣고, 추론 소비자가 가장 최근 프레임을 꺼내 탐지한다.
실행은 [`pipeline`](./pipeline/README.md) 진입점이다.

```text
카메라 ─RTSP─▶ stream ─샘플링─▶ FrameBuffer ─최신 1장─▶ inference ─▶ 로그 + 선택적 HTTP
                                 오래된 것 버림
```

`recorder`는 별도 진입점으로 돈다. MediaMTX에서 직접 RTSP를 받아 세그먼트를 만들고
객체 저장소에 적재한 뒤, 보존 기간이 지난 것을 지운다.

입구 카메라는 `FACE_IDENTITY_URL`과 `FACE_IDENTITY_CAMERA_IDS`를 설정하면 사람 탐지 뒤
deeplearning의 SCRFD·ArcFace 갤러리 식별을 호출한다. 식별된 학생 ID를 포함한 최종
탐지는 `FASTAPI_URL`로 보낸다. 얼굴 식별 서비스 장애는 사람 탐지 전송을 막지 않는다
([결정 0035](../docs/architecture/decisions.md#0035--입구-얼굴-식별은-worker에서-deeplearning-내부-http로-호출한다)).

아직 없는 것은 입구 얼굴 track을 교실 사람 track으로 넘기는 경로다. 따라서 특정 학생을
입구 이벤트에서 식별할 수는 있지만, 그 학생이 어느 좌석에 앉았는지는 이어 말할 수 없다.

## 워커 사이의 경계

- **`stream`은 탐지하지 않는다.** 어떤 프레임을 넘길지까지가 책임이다.
- **`inference`는 모델을 소유하지 않는다.** 프레임을 꺼내 호출하고 실패를 처리하는
  실행 단계이며, 모델 종류·가중치·전처리는 [`deeplearning`](../deeplearning/README.md)이
  가진다([결정 0009](../docs/architecture/decisions.md#0009--추론-책임을-모델과-실행으로-나눈다)).
  얼굴 식별은 내부 HTTP로 경계를 지키고, 사람 탐지만 `inference`가 ultralytics를 직접
  부르는 잠정 예외다.
- **`inference`는 의미를 부여하지 않는다.** `student_001, conf 0.87, bbox`까지가 출력이다.
  `PRESENT` 같은 업무 어휘를 넣지 않는다.
- **`recorder`는 `stream`의 프레임이 아니라 MediaMTX에서 직접 받는다.** 저장 때문에
  추론 경로가 느려지지 않게 하기 위해서다.
- **워커끼리 서로를 import하지 않는다.** 공통 계약은 `shared`에 두고, 누가 누구와
  연결되는지는 `pipeline`에서만 정한다.

## 프레임을 어떻게 넘기는가

수신 속도와 추론 속도가 다르다. 카메라는 20 FPS로 프레임을 내놓고 CPU에서 도는
탐지 모델은 그보다 느리다. 그 차이를 [`shared`의 프레임 버퍼](./shared/README.md)가 흡수한다.

- 버퍼가 가득 차면 **가장 오래된 프레임을 버린다.** 수신은 추론을 기다리지 않는다.
- 소비자는 **가장 최근 프레임만** 가져간다. 밀린 프레임을 추론하면 결과가 계속
  과거를 가리킨다.

우리가 원하는 것은 "모든 프레임"이 아니라 "지금 누가 어느 자리에 있는가"다. 배경은
[결정 0006](../docs/architecture/decisions.md#0006--워커-사이-프레임-전달을-최신-우선-버퍼로-한다)에 있다.

**두 워커는 지금 한 프로세스에서 스레드로 돈다.** 0005가 노린 자원 분리는 당분간
유보한 상태이며, 그 이유와 되돌리는 방법도 0006에 적었다. 얼굴 탐지와 얼굴 인식이
붙으면 추론이 무거워지므로 그때 다시 판단한다.

## 포함하지 않는 것

- 객체 탐지·얼굴 인식 모델 자체와 학습 → [`deeplearning`](../deeplearning/README.md) 책임
- 학생 상태 판정 → [`webapps/fastapi`](../webapps/fastapi/README.md) 책임
- 탐지 결과의 조회 API와 화면 → `webapps/fastapi` 책임
- 영상의 장기 보관 정책 **결정** — 합의 사항이지 기술 결정이 아니다
- 사용자 대상 API 제공

## 다른 서비스와의 관계

- **영상 소스(강의실 카메라 / Jetson)**: 외부 시스템이다. 접속 정보는 환경변수로 주입한다.
- **`deeplearning`**: `inference` worker가 지정된 입구 카메라의 JPEG와 사람 bbox를 내부
  HTTP로 보내 얼굴 식별 결과를 받는다. 모델·갤러리 구현은 worker가 알지 않는다.
- **`fastapi`**: 탐지 결과의 소비자이자 상태 판정 주체다. worker가 HTTP로 전달한다.
- **브라우저**: 제품 API와 탐지 SSE는 `fastapi`를 호출한다. 영상은 허용된 WebRTC
  세션에 한해 MediaMTX에 연결하며 worker를 직접 호출하지 않는다.
- **`monitoring`**: 조립 실행이 프레임 처리량·추론 지연·탐지 신뢰도 지표를 노출하고
  Prometheus가 긁어간다([pipeline README](./pipeline/README.md#지표-노출)).
  카메라 연결 상태 지표는 아직 없다(`예정`).

## 영상 데이터와 메타데이터의 분리

메타데이터는 MongoDB, 영상·얼굴 이미지는 MinIO에 보관한다
([결정 0003](../docs/architecture/decisions.md#0003--메타데이터-저장소로-mongodb-채택),
[0004](../docs/architecture/decisions.md#0004--영상과-얼굴-이미지-저장소로-minio-채택)).

**영상 원본은 저장하지 않는다.** 탐지 시점의 스냅샷만 남긴다
([결정 0028](../docs/architecture/decisions.md#0028--영상-원본을-저장하지-않고-스냅샷만-남긴다)).
공용 GPU 서버의 가용 용량이 약 48 GB인데 1080p 카메라 한 대가 시간당 약 0.9 GB라
상시 녹화가 성립하지 않는다. `recorder`는 코드가 남아 있으나 공용 서버에서 돌리지 않는다.
0007의 상시 녹화와 보존 기간 30일 기본값은 0011로 대체됐다.

스냅샷은 inference가 적재하며 해상도·품질·최소 간격은 설정으로 관리한다. 보존 삭제는
MinIO lifecycle이 수행한다.

`stream`의 로컬 저장은 학습 데이터 확보를 위한 개발용이며 **기본값이 꺼져 있고
`APP_ENV=prod`에서는 켤 수 없다.**

수집한 영상과 프레임은 `worker/**/data/`에 쌓이며 `.gitignore` 대상이다.
**학생 얼굴이 담기므로 어떤 경우에도 커밋하지 않는다.**

## 실행

두 워커를 이어 돌리려면 조립 진입점을 쓴다.

```bash
cd worker
python -m pip install -r pipeline/requirements.txt
cp pipeline/.env.example pipeline/.env.local    # STREAM_SOURCES를 채운다
export APP_ENV=local   # 생략하면 어차피 local로 동작한다
python -m pipeline.main
```

각 워커는 환경마다 달라야 하는 값·비밀값을 `.env.{local,dev,prod}`에, 재시도 횟수·
타임아웃 같은 일반 설정을 커밋된 `config/settings.yml`에 나눠 둔다. 자세한 내용은
[환경변수 규칙](../docs/conventions/environment-convention.md)을 따른다.

워커를 따로 돌릴 수도 있다. 절차와 환경변수는 각 워커 README에 있다.

```bash
python -m stream.main       # 수신만
python -m recorder.main     # 녹화·적재만
python -m pytest -q         # 워커 전체 테스트. 장비도 모델도 MinIO도 필요 없다
```

## 관련 문서

- [조립 진입점](./pipeline/README.md) — 두 워커를 잇는 실행 방법
- [shared](./shared/README.md) — 프레임 버퍼와 공통 타입
- [stream worker](./stream/README.md) — 실행 방법, 환경변수, 테스트
- [inference worker](./inference/README.md) — 실행 단계, 탐지 결과 형식
- [recorder worker](./recorder/README.md) — 녹화·적재, 보존 정책, 객체 키 규칙
- [카메라 수집 구성](./stream/camera-guides.md) — 구성 요소, 설정값, 겪은 문제
- [deeplearning](../deeplearning/README.md) — 모델 책임 범위
- [AI 에이전트 규칙](../docs/agents/ai-agent.md)
- [아키텍처](../docs/architecture/README.md)
- [환경변수 규칙](../docs/conventions/environment-convention.md)
