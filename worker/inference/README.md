# inference worker

`stream` worker가 고른 프레임을 꺼내 모델을 호출하는 **실행 단계**다.

## 이 워커는 모델을 소유하지 않는다

[결정 0009](../../docs/architecture/decisions.md#0009--추론-책임을-모델과-실행으로-나눈다)가
추론 책임을 둘로 나눴다.

| 담당 | 무엇을 아는가 |
| --- | --- |
| [`deeplearning`](../../deeplearning/README.md) | 모델 종류, 가중치, 전처리, 후처리, 탐지 결과 스키마 |
| `worker/inference` | 언제 호출하는가, 실패하면 어떻게 하는가, 언제 멈추는가 |

**현재 코드는 이 경계를 아직 만족하지 않는다.** `deeplearning`에 코드가 없어서
이 워커가 ultralytics를 직접 부른다. 잠정 상태이며, `deeplearning` 구현 시
`model.py`의 모델 호출을 그쪽으로 옮긴다. 새 모델 코드는 여기에 만들지 않는다.

## 현재 상태

YOLOv8n으로 프레임 한 장에서 `person`과 `cell phone`을 탐지한다. 입력은 두 갈래다.

- **프레임 버퍼** — `stream`이 넣은 최신 프레임을 소비자 루프가 꺼내 쓴다.
  실행은 [`pipeline`](../pipeline/README.md) 진입점이다.
- **이미지 파일** — `python -m inference.main <이미지경로>`로 한 장만 확인한다.

**`cell phone` 클래스는 이전 주제(직원 통화 판정)에서 온 것으로 강의실 학생
모니터링에서 쓰이지 않는다.** 모델 이관 때 함께 정리한다.

**아직 없는 것**: 얼굴 탐지와 얼굴 인식(`deeplearning` `예정`), 탐지 결과를
HTTP로 `fastapi`에 넘기는 경로. 방식은
[결정 0011](../../docs/architecture/decisions.md#0011--실시간-관제-전달을-httpwebrtcsse로-구성한다)로
확정됐고 현재는 로그로만 출력한다.
추론 지연·처리량·식별 성공률 지표도 `예정`이다.

## 서비스 목적

프레임을 받아 "무엇이 어디에 있는가"를 좌표와 신뢰도로 돌려준다.
이 워커가 없으면 영상은 흘러갈 뿐 아무 정보도 만들어내지 못한다.

## 구성

| 파일 | 역할 |
| --- | --- |
| `config.py` | 모델 경로·장치·임계값 설정 |
| `types.py` | `Detection`, `InferenceResult` — 탐지 결과 형식 |
| `model.py` | `Yolo8nDetector` — 모델 로딩과 탐지 |
| `processor.py` | 프레임을 모델에 넘기는 경계 |
| `consumer.py` | 프레임 버퍼에서 최신 프레임을 꺼내 도는 소비자 루프 |
| `main.py` | 이미지 파일 한 장을 검사하는 진입점 |

**`stream`을 import하지 않는다.** 소비자는 [`shared`의 프레임 버퍼](../shared/README.md)만
알고, 누가 프레임을 넣는지 모른다.

## 탐지 결과 형식

```python
InferenceResult(
    frame_shape=(480, 640, 3),
    detections=(
        Detection(class_id=0, class_name="person", confidence=0.87, bbox=(12, 30, 220, 470)),
    ),
)
```

`bbox`는 `(x1, y1, x2, y2)` 픽셀 좌표다.

얼굴 인식이 붙으면 여기에 `student_id`와 식별 신뢰도가 더해진다(`예정`).
스키마는 [`deeplearning`](../../deeplearning/README.md)이 소유한다.

**여기까지가 이 워커의 출력이다.** `PRESENT`, `WRONG_SEAT`, `ABSENT` 같은 업무
의미는 붙이지 않는다. 그 해석은 `webapps/fastapi`의 일이다
([결정 0008](../../docs/architecture/decisions.md#0008--학생-상태-판정을-rule-engine으로-분리하고-fastapi가-소유한다)).

**식별하지 못한 것을 억지로 식별하지 않는다.** 신뢰도가 기준 미만이면 `UNKNOWN`으로
두고 가장 가까운 학생을 고르지 않는다. 오인식은 다른 학생의 정보를 노출하는 사고다.

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
| 얼굴 탐지 모델 | `후보`: SCRFD | `deeplearning` 책임 |
| 얼굴 인식 모델 | `후보`: AdaFace R50, ArcFace | 비교 후 결정. `deeplearning` 책임 |
| 실행 장치 | CPU 기본, CUDA 선택 | `INFERENCE_DEVICE`로 고른다 |
| 프레임 수신 | 프레임 버퍼 | [결정 0006](../../docs/architecture/decisions.md#0006--워커-사이-프레임-전달을-최신-우선-버퍼로-한다) |
| 결과 전달 방식 | HTTP | `예정`. 현재는 로그 출력 |

**모델 가중치 파일은 저장소에 커밋하지 않는다.** 경로는 `MODEL_PATH`로 주입한다.
파일이 없으면 ultralytics가 이름을 보고 내려받는다.

**모델은 프로세스 시작 시 한 번만 로딩한다.** 프레임마다 불러오면 추론이 멈춘다.

## 실행 방법

```bash
cd worker
python -m pip install -r inference/requirements.txt
cp inference/.env.example inference/.env

# 이미지 한 장 검사
python -m inference.main path/to/image.jpg

# stream과 이어서 실행
python -m pipeline.main
```

> **직접 확인한 것**: 대역 모델로 탐지 결과 변환과 소비자 루프(최신 프레임 선택,
> 실패 처리, 종료) 동작.
> **확인하지 못한 것**: 실제 YOLOv8n 가중치를 붙인 추론. 이 환경에 `ultralytics`와
> `torch`가 설치되어 있지 않다. 설치한 사람이 확인한 뒤 이 문단을 갱신한다.
> **측정하지 않은 것**: FPS, 추론 지연, 정확도. 측정 전에는 수치를 적지 않는다.

## 환경변수

이름과 용도는 [`.env.example`](./.env.example)에 있다.
조립 실행에서는 이 파일이 아니라 `pipeline/.env`를 읽는다.

| 이름 | 용도 | 비고 |
| --- | --- | --- |
| `MODEL_PATH` | 모델 가중치 경로 | 기본 `yolov8n.pt` |
| `INFERENCE_DEVICE` | 실행 장치 | `cpu` / `cuda` |
| `INFERENCE_CONFIDENCE_THRESHOLD` | 탐지 신뢰도 임계값 | 기본 0.25 |

소비자 루프의 대기 시간과 연속 실패 허용치는 조립 쪽 설정이다.
[pipeline README](../pipeline/README.md#환경변수)를 따른다.

## 실패했을 때

- **추론 1회 실패** — 스택을 로그로 남기고 다음 프레임으로 넘어간다. 프레임 한 장
  때문에 파이프라인 전체를 죽이지 않는다.
- **연속 실패가 한계를 넘음** — 종료 신호를 켜서 수신까지 함께 멈춘다. 계속 실패하는
  상태로 도는 것은 프레임만 버리면서 아무것도 만들지 않는 것과 같다.

## 테스트 전략

기본 테스트는 실제 가중치와 GPU 없이 돈다. 모델 호출을 대역으로 바꾼다.

```bash
cd worker
python -m pytest inference/tests -q
```

- `test_model.py` — 대역 모델로 탐지 결과 변환 검증
- `test_consumer.py` — 최신 프레임 선택, 실패 누적과 중단, 종료 처리

실제 가중치가 필요한 확인은 기본 실행에 넣지 않는다.
측정하지 않은 성능 수치를 문서나 보고에 적지 않는다.
**테스트 자산에 실제 사람의 얼굴을 쓰지 않는다.**

## 관련 문서

- [worker 개요](../README.md)
- [deeplearning](../../deeplearning/README.md) — 모델 책임 범위
- [조립 진입점](../pipeline/README.md) · [shared 프레임 버퍼](../shared/README.md)
- [AI 에이전트 규칙](../../docs/agents/ai-agent.md)
- [아키텍처](../../docs/architecture/README.md)
