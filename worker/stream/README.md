# stream worker

RTSP 영상 소스에 붙어 연결을 유지하고, 추론 대상 프레임을 골라내는 워커다.

## 서비스 목적

카메라는 자주 끊긴다. 끊김을 감지해 다시 붙는 일과, 모든 프레임을 추론에 보내지 않도록
샘플링하는 일을 이 워커가 맡는다. 이게 없으면 추론 서비스가 연결 관리와 부하 조절을
함께 떠안게 된다.

## 책임

- 여러 RTSP 소스와의 연결 수립·유지·재연결
- 소스별 식별자 부여와 연결 상태 관리 (`idle` / `connected` / `reconnecting` / `failed` / `stopped`)
- 프레임 추출과 샘플링 — 모든 프레임을 추론에 보내지 않는다
- 로컬 USB 카메라를 RTSP로 송출 (개발 환경용, 선택)
- 추출한 프레임을 `inference` worker에 공급 (`예정`, 전달 방식 `결정 필요`)
- 연결 상태·프레임 처리량 지표 노출 (`예정`)

## 포함하지 않는 것

- 객체 탐지·모델 추론 → `inference` worker 책임
- 탐지 결과의 업무 해석 → `state` worker 또는 `fastapi` 책임 (`결정 필요`)
- 영상의 운영 보관 → `recorder` worker 책임
- 사용자 대상 API·화면 제공 → `fastapi` 책임

## 구성

| 파일 | 역할 |
| --- | --- |
| `config.py` | 환경변수 읽기와 시작 시 검증. 소스 목록 파싱 |
| `errors.py` | `CameraConnectionError` 등 도메인 예외 |
| `camera_reader.py` | RTSP 소스 하나의 연결·재연결·프레임 읽기 |
| `rtsp_publisher.py` | FFmpeg으로 USB 카메라를 RTSP로 송출 (선택) |
| `video_recorder.py` | 원본 영상 세그먼트 저장 (개발용, 기본 꺼짐) |
| `frame_capture.py` | 샘플링한 프레임을 이미지로 저장 (개발용, 기본 꺼짐) |
| `worker.py` | 카메라별 파이프라인을 스레드로 관리 |
| `main.py` | 진입점. 설정 검증, 로깅, 종료 신호 처리 |

**카메라 대수만큼 프로세스를 띄우지 않는다.** `StreamWorker` 하나가 소스마다
`CameraPipeline` 스레드를 들고 있고, 한 카메라의 연결 실패가 다른 카메라를 멈추지 않는다.
OpenCV의 프레임 읽기는 GIL을 놓는 블로킹 호출이라 스레드로 병행된다.

## 기술

| 항목 | 상태 | 비고 |
| --- | --- | --- |
| 언어 | Python | 3.12 이상 |
| 수신 프로토콜 | RTSP (TCP) | [결정 0007](../../docs/architecture/decisions.md#0007--worker를-역할별-워커로-분리) |
| 스트림 서버 | MediaMTX | 같은 결정 |
| 프레임 처리 | OpenCV | 같은 결정 |
| 다중 카메라 | 스레드 | 같은 결정 |
| 추론 전달 방식 | `결정 필요` | 동기 호출 / 공유 저장소 / 큐 |

## 실행 방법

FFmpeg과 MediaMTX는 pip 패키지가 아니다. 시스템에 따로 설치한다.
USB 카메라를 직접 쓰지 않고 이미 RTSP를 내보내는 소스에 붙는다면 FFmpeg은 필요 없다.

```bash
cd worker
python -m pip install -r stream/requirements.txt
cp stream/.env.example stream/.env    # STREAM_SOURCES를 채운다
python -m stream.main
```

`python -m stream.main`은 `worker` 디렉터리에서 실행한다. `stream`이 패키지라
파일을 직접 실행(`python stream/main.py`)하면 import가 깨진다.

종료는 `Ctrl+C`다. `SIGINT`·`SIGTERM`을 받으면 스레드를 정리하고 FFmpeg을 종료한다.

> **직접 확인한 것**: 필수 환경변수가 없을 때 종료 코드 1로 즉시 멈추는 것,
> `APP_ENV=prod`에서 저장을 켜면 거부하는 것, 단위 테스트 74개 통과.
> **확인하지 못한 것**: 실제 카메라·FFmpeg·MediaMTX를 붙인 수집 동작.
> 장비가 있는 사람이 확인한 뒤 이 문단을 갱신한다.

동작 확인은 화면 출력이 아니라 로그와 RTSP 재생으로 한다. 이 워커는 헤드리스로
돌기 때문에 미리보기 창을 띄우지 않는다. 영상이 실제로 흐르는지는 VLC 같은
클라이언트로 `STREAM_SOURCES`의 URL을 열어 확인한다.

## 환경변수

이름과 용도는 [`.env.example`](./.env.example)에 있다. **실제 값은 커밋하지 않는다.**
카메라 접속 정보는 비밀값 등급이다([환경변수 규칙](../../docs/conventions/environment-convention.md)).

| 이름 | 용도 | 비고 |
| --- | --- | --- |
| `APP_ENV` | 실행 환경 | `local` / `dev` / `prod`. 필수 |
| `STREAM_SOURCES` | 연결할 영상 소스 목록 | `<식별자>=<RTSP URL>`을 쉼표로 구분. 필수 |
| `STREAM_RECONNECT_MAX_RETRY` | 재연결 최대 시도 횟수 | 기본 10 |
| `STREAM_RECONNECT_DELAY_SECONDS` | 재연결 시도 간격 | 기본 1.0 |
| `STREAM_STARTUP_WAIT_SECONDS` | RTSP 경로 생성 대기 | 기본 3.0 |
| `STREAM_READ_FAILURE_TOLERANCE` | 재연결을 부르는 연속 실패 횟수 | 기본 30 |
| `FRAME_SAMPLE_INTERVAL_FRAMES` | 샘플링 주기(프레임 수) | 기본 20 |
| `RTSP_PUBLISH_ENABLED` | USB 카메라 RTSP 송출 여부 | 기본 false |
| `RTSP_PUBLISH_INPUT_FORMAT` | FFmpeg 입력 형식 | `dshow` / `v4l2` / `avfoundation` |
| `RTSP_PUBLISH_DEVICE_NAME` | 카메라 장치 이름 | 송출 시 필수 |
| `RTSP_PUBLISH_TARGET_URL` | 송출 대상 RTSP URL | 송출 시 필수 |
| `RTSP_PUBLISH_FRAMERATE` | 송출 프레임률 | 기본 20 |
| `RECORDING_ENABLED` | 원본 영상 로컬 저장 | 기본 false. `prod`에서 금지 |
| `RECORDING_OUTPUT_DIR` | 영상 저장 경로 | 기본 `stream/data/video` |
| `RECORDING_FPS` | 저장 영상 FPS | 기본 20 |
| `RECORDING_SEGMENT_SECONDS` | 영상 파일 하나의 길이 | 기본 3600 |
| `FRAME_CAPTURE_ENABLED` | 학습용 프레임 저장 | 기본 false. `prod`에서 금지 |
| `FRAME_CAPTURE_OUTPUT_DIR` | 프레임 저장 경로 | 기본 `stream/data/frames` |
| `LOG_LEVEL` | 로그 수준 | 기본 `INFO` |

### 저장 기능이 기본으로 꺼져 있는 이유

영상에는 사무실 구성원의 얼굴이 담긴다. 저장 범위·보존 기간·접근 권한이
합의되기 전까지 상시 저장 기능을 만들지 않기로 했다
([결정 0004](../../docs/architecture/decisions.md#0004--영상스냅샷-저장소로-minio-채택)).
학습 데이터 수집이 필요한 개발 환경에서만 명시적으로 켠다.
`APP_ENV=prod`에서 켜면 프로세스가 시작 시점에 거부한다.

## 테스트 전략

기본 테스트는 실제 카메라·FFmpeg·MediaMTX 없이 돈다. OpenCV `VideoCapture`와
`subprocess.Popen`은 테스트 대역으로 갈아 끼운다.

```bash
cd worker
python -m pytest stream/tests -q
```

- 연결 상태 전이(연결 → 끊김 → 재시도 → 복구)를 대역으로 검증한다.
- 프레임 샘플링은 순수 함수(`should_sample`)로 분리해 고정 입력으로 검증한다.
- 오류 메시지에 카메라 자격 증명이 새지 않는지 검증한다.
- 실제 장비가 필요한 확인은 기본 실행에 넣지 않는다.

## 관련 문서

- [worker 개요](../README.md) — 워커 구성과 경계
- [카메라 수집 구성](./camera-guides.md) — 구성 요소, 설정값, 겪은 문제
- [AI 에이전트 규칙](../../docs/agents/ai-agent.md)
- [환경변수 규칙](../../docs/conventions/environment-convention.md)
- [코딩 규칙](../../docs/conventions/coding-convention.md)
