# worker

카메라 영상을 수신하고, 추론 가능한 형태의 프레임으로 공급하는 서비스 디렉터리다.

> **범위 주의**: 이름은 `worker`지만 담당은 **영상 스트림 처리로 한정**한다.
> 큐 소비, 배치 작업 같은 일반 백그라운드 작업은 고려 대상이 아니다.
> 그런 작업이 필요해지면 여기에 끼워 넣기 전에 책임 분리를 먼저 검토한다.

## 현재 상태

USB 카메라 1대를 기준으로 **수집 → RTSP 송출 → 수신 → 로컬 저장**까지 동작한다.
스크립트 형태이며 서비스로 상시 실행되지는 않는다.

```text
USB 카메라 → FFmpeg → MediaMTX(RTSP) → OpenCV ┬→ 원본 영상 저장
                                              └→ 학습용 프레임 이미지 저장
```

**아직 없는 것**: `deeplearning`으로의 프레임 공급, `fastapi`로의 상태 노출,
MinIO 업로드, 재연결·자동 복구, 다중 카메라, 테스트.

구성 요소별 역할, 실제 설정값, 겪은 문제와 대응은
[카메라 수집 구성](./camera-guides.md)에 있다.

| 파일 | 역할 |
| --- | --- |
| `config.py` | 카메라 이름, RTSP URL, 저장 경로, FPS, 프레임 간격 |
| `camera_stream.py` | FFmpeg subprocess로 USB 카메라를 RTSP로 송출 |
| `camera.py` | RTSP 클라이언트. 연결과 프레임 읽기 |
| `video_recorder.py` | 원본 영상 저장 (`data/video`) |
| `frame_capture.py` | 학습용 프레임 이미지 저장 (`data/frames`) |
| `camera_run.py` | 위 구성 요소를 순서대로 실행하는 진입점 |

`data/video`와 `data/frame`은 `.gitignore` 대상이다. 수집한 영상과 프레임을
커밋하지 않는다.

## 실행

FFmpeg과 MediaMTX가 시스템에 설치되어 있어야 하고, `config.py`의 `CAMERA_NAME`이
실제 장치 이름과 같아야 한다. 현재 값은 Windows DirectShow 장치 이름 기준이다.

```bash
cd worker
python camera_run.py
```

> 위 명령은 `camera_run.py`의 진입 형태에서 옮긴 것이며 **이 문서를 쓰면서 실행해
> 확인하지는 않았다.** 의존성 목록(`requirements.txt`)과 MediaMTX 기동 절차도 아직
> 정리되지 않았다. 실제로 실행해 본 사람이 이 절을 확인된 절차로 바꾼다.

## 책임

**실시간 영상을 받아 저장소와 객체 탐지 모델에 넘기는 것이 이 서비스의 일이다.**

- 카메라·장치와의 스트림 연결 수립 및 유지
- 연결 끊김 감지와 재연결 (`예정`)
- 프레임 추출과 샘플링 — 모든 프레임을 추론에 보내지 않는다
- 추출한 프레임을 `deeplearning`에 공급 (`예정`)
- 영상·스냅샷을 저장소에 적재 (`예정`, 저장 범위 합의 후)
- 연결 상태 및 프레임 처리량 지표 노출 (`예정`)

## 포함하지 않는 것

- 객체 탐지·모델 추론 → [`deeplearning`](../deeplearning/README.md) 책임
- 탐지 결과에 대한 업무 판단 → [`webapps/fastapi`](../webapps/fastapi/README.md) 책임
- 영상의 장기 보관 정책 결정과 저장 실행
- 사용자 대상 API 제공

## 다른 서비스와의 관계

- **영상 소스(USB 카메라 / CCTV / Jetson)**: 외부 시스템이다. 접속 정보는 환경변수로 주입한다.
- **`deeplearning`**: 프레임 공급 대상이다. 전달 방식이 `결정 필요`이므로 임의로 구현하지 않는다.
- **`fastapi`**: 스트림 연결 상태를 조회할 수 있게 한다. 조회 경로는 `결정 필요`.
- **브라우저**: 직접 호출하지 않는다. `fastapi`를 통해서만 상태를 조회한다.
- **`monitoring`**: 연결 상태·프레임 처리량 지표를 노출한다 (`예정`).

## 영상 데이터와 메타데이터의 분리

메타데이터는 MongoDB, 영상·스냅샷은 MinIO에 보관한다
([결정 0003](../docs/architecture/decisions.md#0003--메타데이터-저장소로-mongodb-채택),
[0004](../docs/architecture/decisions.md#0004--영상스냅샷-저장소로-minio-채택)).

**영상을 저장소로 넘기는 주체는 worker다.** 다만 저장 범위와 보존 기간이 아직
합의되지 않았으므로, 확정 전까지 영상을 상시 저장하는 기능을 만들지 않는다.
현재 로컬 저장은 학습 데이터 확보를 위한 개발용이며 운영 보관 수단이 아니다.

## 설정과 환경변수

현재 설정값은 `config.py`에 상수로 들어 있다. 접속 자격 증명이 필요해지는 시점에
환경변수로 옮긴다. 값의 취급과 명명 규칙은
[환경변수 규칙](../docs/conventions/environment-convention.md)을 따르고,
**카메라 접속 자격 증명은 어떤 형태로도 저장소에 두지 않는다.**

| 이름 | 용도 | 상태 |
| --- | --- | --- |
| `STREAM_SOURCES` | 연결할 영상 소스 목록 | `예정` |
| `STREAM_CREDENTIALS_*` | 소스 접속 자격 증명 | `예정`. 값은 커밋 금지 |
| `FRAME_SAMPLE_INTERVAL` | 프레임 샘플링 주기 | 현재 `config.FRAME_INTERVAL` |
| `RECONNECT_MAX_RETRY` | 재연결 최대 시도 횟수 | 현재 `config.MAX_RETRY` |
| `INFERENCE_TARGET` | 프레임 전달 대상 | `예정`. 전달 방식 확정 후 |

## 테스트 전략

아직 테스트가 없다. 추가할 때는 다음을 따른다.

- 연결 상태 전이(연결 → 끊김 → 재시도 → 복구)를 실제 장비 없이 단위 테스트한다.
- 프레임 샘플링 로직은 고정 입력으로 검증한다.
- 실제 카메라가 필요한 테스트는 분리하고 기본 실행에 포함하지 않는다.

## 관련 문서

- [카메라 수집 구성](./camera-guides.md) — 구성 요소, 설정값, 겪은 문제
- [AI 에이전트 규칙](../docs/agents/ai-agent.md)
- [아키텍처](../docs/architecture/README.md)
- [환경변수 규칙](../docs/conventions/environment-convention.md)
