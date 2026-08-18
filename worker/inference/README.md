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

**아직 없는 것**: 얼굴 탐지와 얼굴 인식(`deeplearning` `예정`). HTTP 전달 경로는
구현되어 `pipeline`의 `FASTAPI_URL`을 설정하면 `/internal/inference/events`로 전송한다.
설정하지 않으면 기존처럼 로그만 출력한다. 추론 지연·처리량·식별 성공률 지표도 `예정`이다.

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
| `handler.py` | 결과를 FastAPI 내부 API 계약으로 직렬화하고 제한 재시도 |
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
        ),
    ),
)
```

`bbox`와 `face_bbox`는 원본 프레임 기준 `(x1, y1, x2, y2)` 픽셀 좌표다. 얼굴 인식이
붙으면 `student_id`와 `identity_confidence`를 함께 채운다. 미식별이면 세 신원 필드를
모두 비우며, 불완전한 조합은 HTTP payload에서 안전하게 미식별로 낮춘다. 전체 내부 API
계약과 검증 방법은 [모델 연동 인계](./MODEL_INTEGRATION.md)를 따른다.

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
| 얼굴 탐지 모델 | `후보`: SCRFD | `deeplearning` 책임 |
| 얼굴 인식 모델 | `후보`: AdaFace R50, ArcFace | 비교 후 결정. `deeplearning` 책임 |
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
| `INFERENCE_DEVICE` | 실행 장치 | `cpu` / `cuda`. local은 보통 cpu, dev는 cuda |
| `OBJECT_STORAGE_BACKEND` | 객체 저장소 종류 | `local` / `minio`. local은 보통 `local` |
| `OBJECT_STORAGE_ENDPOINT`, `_ACCESS_KEY`, `_SECRET_KEY` | MinIO 접속 정보 | `minio` backend에서만 필요. 비밀값 |

### `config/settings.yml`

| 이름 | 용도 | 비고 |
| --- | --- | --- |
| `inference_confidence_threshold` | 탐지 신뢰도 임계값 | 기본 0.25 |
| `snapshot_enabled` | 탐지 스냅샷 적재 | 기본 `false`. 저장은 명시적으로 켠다 |
| `snapshot_max_long_side_px` | 긴 변 상한 | 기본 1280(720p). 확대하지 않는다 |
| `snapshot_jpeg_quality` | JPEG 품질 | 기본 80 |
| `snapshot_min_interval_seconds` | 카메라당 최소 적재 간격 | 기본 60 |
| `object_storage_bucket`, `_secure`, `_timeout_seconds` | 버킷 이름·TLS·타임아웃 | `snapshot_enabled: true`일 때만 필요 |

소비자 루프의 대기 시간과 연속 실패 허용치는 조립 쪽 설정이다.
[pipeline README](../pipeline/README.md#환경변수와-설정)를 따른다.

## 탐지 스냅샷

**영상 원본을 저장하지 않는다.** 대신 탐지 시점의 정지 이미지를 객체 저장소에 남긴다
([결정 0011](../../docs/architecture/decisions.md#0011--영상-원본을-저장하지-않고-스냅샷만-남긴다)).
공용 GPU 서버의 가용 용량이 약 48 GB인데 1080p 카메라 한 대가 시간당 약 0.9 GB라
상시 녹화가 성립하지 않는다.

프레임을 이미 들고 있는 쪽이 이 워커라서 여기서 만든다. `recorder`처럼 RTSP를 따로
받지 않으므로 추가 디코딩이 없다.

**적재 조건은 둘 다 만족해야 한다.**

1. 탐지 **개수**가 직전에 *올린* 개수와 다르다
2. 그 카메라의 마지막 적재에서 `SNAPSHOT_MIN_INTERVAL_SECONDS`가 지났다

1번이 "마지막으로 본" 개수가 아니라 "마지막으로 올린" 개수인 것이 중요하다. 간격 캡에
막혀 건너뛴 변화가 다음 기회에 그대로 올라간다. 아직 아무것도 올리지 않았으면 0을
기준으로 보므로, 기동 직후 아무도 없는 화면을 한 장 올리는 일이 없다.

**2번이 없으면 용량 계산이 무너진다.** 탐지가 경계에서 떨릴 때(2명 ↔ 3명 반복)
적재가 폭주한다. 상한 없는 적재 경로를 만들지 않는다.

**업무 의미를 붙이지 않는다.** 여기서 보는 것은 개수 변화까지다. "학생이 자리를
비웠다" 같은 해석은 `fastapi`가 한다(결정 0008·0009).

적재에 실패하면 로그만 남기고 넘어간다. 상태를 갱신하지 않으므로 다음 프레임에서
다시 시도한다. **저장소 장애가 탐지를 멈추지 않는다.**

객체 키는 `<카메라>/<날짜>/<시각>.jpg`이며 규칙은
[`shared/object_keys.py`](../shared/object_keys.py)에 있다. 보존 기간 삭제는 이 워커가
하지 않는다 — 저장소의 lifecycle 규칙이 한다.

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
- `test_handler.py` — 승인 fixture와 payload 일치, 멱등 ID, HTTP 제한 재시도 검증

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
