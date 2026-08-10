# pipeline

**워커가 아니다.** `stream`과 `inference`를 한 프로세스에서 잇는 조립 진입점이다.

## 무엇을 하는가

```text
[pipeline 프로세스]

  camera-01 스레드 ─┐
  camera-02 스레드 ─┼─put─▶ FrameBuffer ─get_latest─▶ inference-consumer 스레드
  camera-03 스레드 ─┘        (오래된 것 버림)              │
                                                          ▼
                                                      YOLOv8n
                                                          │
                                                          ▼
                                                    탐지 결과 로그
```

설정을 읽고 객체를 조립하는 코드를 여기 한 곳에 모은다. 워커 안에서 서로를
직접 조립하면 나중에 추론을 별도 프로세스로 뗄 때 고칠 곳이 흩어진다.
`app/shared/dependencies.py`가 fastapi에서 하는 일과 같다.

**stream과 inference는 서로를 import하지 않는다.** 둘 다 `shared`의 버퍼만 안다.

## 실행 방법

```bash
cd worker
python -m pip install -r pipeline/requirements.txt
cp pipeline/.env.example pipeline/.env    # STREAM_SOURCES를 채운다
python -m pipeline.main
```

조립 실행은 워커별 `.env`가 아니라 **`pipeline/.env` 하나만** 읽는다.
같은 변수가 여러 파일에 흩어져 어느 값이 적용됐는지 모르게 되는 것을 막는다.
워커를 따로 실행할 때는 각 워커의 `.env`를 쓴다.

종료는 `Ctrl+C`다. `SIGINT`·`SIGTERM`을 받으면 버퍼를 닫아 추론 소비자를 깨우고,
카메라 스레드를 정리한 뒤 프레임 처리량을 로그로 남긴다.

> **직접 확인한 것**: 필수 환경변수가 없을 때 종료 코드 1로 멈추는 것,
> ultralytics가 없을 때 무엇을 설치해야 하는지 알리고 멈추는 것,
> 수신 → 샘플링 → 버퍼 → 소비자까지 실제 컴포넌트로 잇는 통합 테스트.
> **확인하지 못한 것**: 실제 카메라와 실제 YOLO 가중치를 붙인 동작.
> 장비와 모델이 있는 사람이 확인한 뒤 이 문단을 갱신한다.

## 환경변수

전체 목록은 [`.env.example`](./.env.example)에 있다. 조립 전용 값만 아래에 적는다.
수신·추론 값은 [stream README](../stream/README.md#환경변수)와
[inference README](../inference/README.md#환경변수)가 기준이다.

| 이름 | 용도 | 비고 |
| --- | --- | --- |
| `FRAME_BUFFER_MAXSIZE` | 버퍼에 담아둘 최대 프레임 수 | 기본 1 |
| `INFERENCE_POLL_TIMEOUT_SECONDS` | 소비자가 종료 신호를 확인하는 주기 | 기본 0.5 |
| `INFERENCE_MAX_CONSECUTIVE_FAILURES` | 연속 추론 실패 허용 횟수 | 기본 5 |

### 버퍼 크기를 1로 두는 이유

실시간 파이프라인에서 필요한 것은 **지금 화면**이다. 버퍼를 키우면 추론이 밀릴 때
오래된 프레임을 들고 있다가 뒤늦게 처리하게 되고, 결과가 가리키는 시점이 계속
과거로 밀린다. 추론 시간의 편차를 흡수해야 하는 경우에만 2 이상으로 올린다.

버린 프레임 수는 종료 시 로그에 남는다. `dropped`가 계속 늘면 추론이 수신을
못 따라가고 있다는 뜻이므로, 버퍼를 키울 게 아니라 `FRAME_SAMPLE_INTERVAL_FRAMES`를
늘리거나 추론을 GPU로 옮기는 것이 맞다.

## 실패했을 때

| 상황 | 동작 |
| --- | --- |
| 필수 환경변수 없음 | 시작 시점에 변수 이름을 알리고 종료 코드 1 |
| ultralytics 미설치·가중치 없음 | 무엇을 설치할지 알리고 종료 코드 1 |
| 카메라 한 대 연결 실패 | 그 카메라만 재연결을 반복한다. 다른 카메라는 계속 돈다 |
| 추론 1회 실패 | 스택을 로그로 남기고 다음 프레임으로 넘어간다 |
| 추론 연속 실패가 한계 초과 | 파이프라인 전체를 멈춘다. 프레임만 버리며 도는 상태를 두지 않는다 |

## 한 프로세스로 두는 것은 잠정 선택이다

결정 0007은 워커를 단계별로 나눈 이유로 "추론은 GPU에, 수신은 네트워크에 묶이므로
자원을 따로 준다"를 들었다. 한 프로세스에서 돌리면 그 분리를 당분간 못 한다.
지금은 파이프라인을 먼저 잇는 것을 택했고, 배경과 다음 단계는
[결정 0008](../../docs/architecture/decisions.md#0008--워커-사이-프레임-전달을-최신-우선-버퍼로-한다)에 있다.

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
