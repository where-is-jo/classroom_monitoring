# worker

카메라 영상을 받아 추론과 저장으로 넘기는 백그라운드 서비스 묶음이다.

> **범위 주의**: 담당은 **영상 파이프라인으로 한정**한다.
> 큐 소비, 배치 작업 같은 일반 백그라운드 작업은 고려 대상이 아니다.
> 그런 작업이 필요해지면 여기에 끼워 넣기 전에 책임 분리를 먼저 검토한다.

## 워커 구성

영상 파이프라인의 단계별로 워커를 나눈다. 단계마다 멈추는 이유와 확장해야 할 자원이
다르기 때문이다. 배경은 [결정 0007](../docs/architecture/decisions.md#0007--worker를-역할별-워커로-분리)에 있다.

```text
[Jetson / USB 카메라] ─RTSP─→ [MediaMTX]
                                   │
                                   ▼
                            ① stream worker          영상 수신 · 재연결 · 프레임 샘플링
                                   │
                     ┌─────────────┴─────────────┐
                     ▼                           ▼
              ② inference worker           ③ recorder worker
                 사람·수화기 탐지               영상 세그먼트 → MinIO
                     │
                     ▼
              ④ state worker
                 근무중·통화중·부재중·외근 판정
                     │
                     ▼
              MongoDB / fastapi
```

| 디렉터리 | 책임 | 상태 |
| --- | --- | --- |
| [`stream`](./stream/README.md) | RTSP 영상 수집, 연결 유지·재연결, 프레임 샘플링 | 동작 |
| [`inference`](./inference/README.md) | 사람·수화기 탐지 | `예정`. 코드 없음 |
| [`state`](./state/README.md) | 탐지 결과로 직원 상태 판정 | `예정`. 코드 없음. 소유 서비스 `결정 필요` |
| [`recorder`](./recorder/README.md) | 영상 세그먼트를 MinIO에 적재 | `예정`. 코드 없음. 저장 범위 미합의 |

**워커를 카메라 대수만큼 띄우지 않는다.** 워커 애플리케이션 하나가 내부에서 여러
스트림을 스레드로 관리한다. 카메라 3대를 붙이려고 프로세스를 3개 띄우면 설정과
장애 지점이 3벌로 늘어난다.

## 현재 상태

`stream`만 동작한다. USB 카메라 또는 RTSP 소스에서 **수신 → 연결 유지 → 프레임 샘플링**
까지 하며, 개발용으로 원본 영상과 학습용 프레임을 로컬에 저장할 수 있다(기본 꺼짐).

**아직 없는 것**: `inference`로의 프레임 공급, `fastapi`로의 상태 노출,
MinIO 업로드, 상태 판정, 지표 노출.

## 워커 사이의 경계

- **`stream`은 탐지하지 않는다.** 어떤 프레임을 넘길지까지가 책임이다.
- **`inference`는 의미를 부여하지 않는다.** `person 1, conf 0.87`까지가 출력이다.
- **`state`는 판정만 한다.** 저장과 조회는 `fastapi`가 한다.
- **`recorder`는 `stream`의 프레임이 아니라 MediaMTX에서 직접 받는다.** 저장 때문에
  추론 경로가 느려지지 않게 하기 위해서다.

## 포함하지 않는 것

- 객체 탐지 모델 자체와 학습 → [`deeplearning`](../deeplearning/README.md) 책임
- 탐지 결과의 조회 API와 화면 → [`webapps/fastapi`](../webapps/fastapi/README.md) 책임
- 영상의 장기 보관 정책 **결정** — 합의 사항이지 기술 결정이 아니다
- 사용자 대상 API 제공

## 다른 서비스와의 관계

- **영상 소스(USB 카메라 / CCTV / Jetson)**: 외부 시스템이다. 접속 정보는 환경변수로 주입한다.
- **`deeplearning`**: `inference` worker가 모델을 불러 쓴다. 경계는 `결정 필요`.
- **`fastapi`**: 상태와 탐지 결과의 최종 소비자다. 전달 방식은 `결정 필요`.
- **브라우저**: 직접 호출하지 않는다. `fastapi`를 통해서만 상태를 조회한다.
- **`monitoring`**: 연결 상태·프레임 처리량 지표를 노출한다 (`예정`).

## 영상 데이터와 메타데이터의 분리

메타데이터는 MongoDB, 영상·스냅샷은 MinIO에 보관한다
([결정 0003](../docs/architecture/decisions.md#0003--메타데이터-저장소로-mongodb-채택),
[0004](../docs/architecture/decisions.md#0004--영상스냅샷-저장소로-minio-채택)).

**영상을 저장소로 넘기는 주체는 worker다.** 다만 저장 범위와 보존 기간이 아직
합의되지 않았으므로, 확정 전까지 영상을 상시 저장하는 기능을 만들지 않는다.
`stream`의 로컬 저장은 학습 데이터 확보를 위한 개발용이며 **기본값이 꺼져 있고
`APP_ENV=prod`에서는 켤 수 없다.**

수집한 영상과 프레임은 `worker/**/data/`에 쌓이며 `.gitignore` 대상이다.
얼굴이 담기므로 어떤 경우에도 커밋하지 않는다.

## 관련 문서

- [stream worker](./stream/README.md) — 실행 방법, 환경변수, 테스트
- [카메라 수집 구성](./stream/camera-guides.md) — 구성 요소, 설정값, 겪은 문제
- [AI 에이전트 규칙](../docs/agents/ai-agent.md)
- [아키텍처](../docs/architecture/README.md)
- [환경변수 규칙](../docs/conventions/environment-convention.md)
