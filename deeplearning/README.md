# deeplearning

컴퓨터 비전 모델을 실행해 영상 프레임에서 **사람을 찾고, 얼굴을 찾고, 학생을 식별**하는
추론 디렉터리다.

> 현재 상태: SCRFD 얼굴 검출과 MediaPipe 자세 분석 내부 HTTP 서비스가 구현됐다.
> 기본 속도·특징점 안정성·흐림·밝기·중복 품질 수치는 구현되었다. 전용 가림 모델과 AdaFace 인식은 아직 구현되지 않았다.
> 모델 학습용 Jupyter 노트북은 [`training/`](./training/README.md)에 있다
> ([결정 0029](../docs/architecture/decisions.md#0029--deeplearning에-모델-학습용-jupyter-노트북-도구를-둔다)).
> 성능 수치나 정확도를 측정 없이 이 문서에 기록하지 않는다.

## 서비스 목적

전달받은 프레임 또는 이미지에 대해 모델 추론을 수행하고, 표준화된 탐지 결과를 반환한다.
결과의 해석과 저장은 담당하지 않는다.

**모델을 아는 유일한 곳이다.** 모델을 바꿔도 `worker`와 `fastapi`를 고치지 않게 하는 것이
이 경계의 목적이다([결정 0009](../docs/architecture/decisions.md#0009--추론-책임을-모델과-실행으로-나눈다)).

## worker/inference와의 경계

| 담당 | 무엇을 아는가 |
| --- | --- |
| `deeplearning` | 모델 종류, 가중치, 전처리, 후처리, 탐지 결과 스키마 |
| [`worker/inference`](../worker/inference/README.md) | 언제 호출하는가, 실패하면 어떻게 하는가, 언제 멈추는가 |

**현재 코드는 이 경계를 만족하지 않는다.** 이 디렉터리에 코드가 없어서
`worker/inference`가 ultralytics를 직접 부른다. 구현 시 그 모델 호출을 이 디렉터리로
옮긴다. 새 모델 코드는 `worker`가 아니라 여기에 만든다.

## 목표 파이프라인

```text
프레임
  │
  ▼ Person Detection            사람이 어디 있는가
사람 ROI
  │
  ▼ Face Detection              그 사람의 얼굴이 어디 있는가
얼굴 crop ──얼굴 없음──▶ 미식별 결과
  │
  ▼ Face Recognition            얼굴을 벡터로
embedding
  │
  ▼ Face Gallery 대조           등록된 학생 중 누구인가
student_id + 신뢰도 ──기준 미만──▶ 신원 필드 없음
```

**출력은 여기까지다.** `PRESENT`, `WRONG_SEAT`, `ABSENT` 같은 업무 의미를 붙이지 않고,
탐지 결과 스키마에 그런 어휘를 넣지 않는다. 해석은 `webapps/fastapi`가 한다
([결정 0008](../docs/architecture/decisions.md#0008--학생-상태-판정을-rule-engine으로-분리하고-fastapi가-소유한다)).

## 책임

- 모델 로딩과 생명주기 관리(프로세스 시작 시 1회 로딩 원칙)
- 프레임·이미지 전처리와 사람 ROI crop
- 사람 탐지, 얼굴 탐지, 얼굴 인식 추론 실행
- 얼굴 등록 샘플의 embedding 생성
- Face Gallery 대조와 신뢰도 산출
- 표준화된 탐지 결과 스키마로 변환
- 추론 지연·처리량·식별 성공률 지표 노출

## 포함해야 할 기능

- 모델 로더(모델 경로와 버전을 설정으로 주입)
- 추론 진입점(입력 → 탐지 결과)
- 입출력 스키마 정의
- 임계값(threshold) 등 추론 파라미터의 설정화
- CPU / GPU 실행 경로 분기
- 얼굴 품질 판정에 쓸 수치 산출(흐림, 밝기, 얼굴 크기)

## 포함하지 않아야 할 기능

- 사용자 인증·권한 판정
- 탐지 결과의 비즈니스 해석(예: "재석으로 간주") → `webapps/fastapi` 책임
- 탐지 결과와 embedding의 영속 저장 → `webapps/fastapi` 책임
- 스트림 연결 유지와 프레임 수명 관리 → [`worker`](../worker/README.md) 책임
- 모델 가중치 파일의 저장소 커밋
- 자동 재학습 파이프라인, 데이터셋 라벨링 도구, 얼굴 데이터를 다루는 학습 — 여전히
  이 프로젝트 범위 밖이다. `training/`의 수동 Jupyter 노트북만 예외이며, 사람 탐지
  모델 fine-tuning까지만 다룬다([결정 0029](../docs/architecture/decisions.md#0029--deeplearning에-모델-학습용-jupyter-노트북-도구를-둔다))

## 모델 선정

**아직 정해지지 않았다.** 후보를 같은 조건으로 비교한 뒤
[결정 기록](../docs/architecture/decisions.md)에 항목을 추가한다.

| 단계 | 후보 | 상태 |
| --- | --- | --- |
| Person Detection | YOLO11n/s, YOLOv8n(현재 코드) | 버전 `결정 필요` |
| Face Detection | SCRFD | `후보` |
| Face Recognition | AdaFace R50, ArcFace | 비교 후 결정 |
| Head Pose | Landmark/Pose 모델 | `후보`. 얼굴 등록 시점의 각도 보정용 |
| Tracking | ByteTrack | **MVP 핵심 경로.** 신원 유지가 여기에 걸린다([결정 0025](../docs/architecture/decisions.md#0025--강의실-안-신원-유지를-bytetrack-트래킹으로-하고-인계-실패는-unknown으로-둔다)). 구현 위치가 `deeplearning`인지 `worker/inference`인지는 `결정 필요` |
| 카메라 간 신원 인계 | CCTV 문 영역 + 통과 시각 기반 인계 / 복장·외형 re-ID | **방법 `결정 필요`.** 0025의 최우선 항목. 두 화각이 겹치지 않아 겹침 기반 인계는 배제됐다 |
| Super Resolution | 별도 모델 | **핵심 경로에서 빠졌다.** 얼굴 인식을 입구에서만 한다([결정 0024](../docs/architecture/decisions.md#0024--카메라-구성을-전체-조망-cctv와-입구-카메라로-바꾸고-학생-식별을-입구-1회로-한정한다)). 아래 참고 |

### 작은 얼굴 문제를 Super Resolution으로 먼저 풀지 않는다

강의실에서 뒷자리 학생의 얼굴은 작게 잡힌다. 그 문제의 해결 순서는 다음과 같다.

1. 카메라 위치 개선
2. 해상도 · 렌즈 · 화각 조정
3. 작은 얼굴 탐지기(SCRFD 등) 검증
4. crop 파이프라인 개선
5. 그래도 부족하면 Super Resolution 검토

모델을 하나 더 붙이는 것이 가장 비싸고, 앞 네 단계가 더 큰 효과를 내는 경우가 많다.

## 모델 학습

공용 GPU 서버에서 팀원이 Jupyter 노트북으로 셀 단위로 실행하며 모델을 학습하는 절차는
[`training/`](./training/README.md)에 있다([결정 0029](../docs/architecture/decisions.md#0029--deeplearning에-모델-학습용-jupyter-노트북-도구를-둔다)).

- **범위는 사람 탐지(Person Detection) 모델의 수동 fine-tuning까지다.** 얼굴 탐지·인식
  모델 학습은 아직 없다 — 개인정보 합의가 먼저 필요하다([얼굴 데이터 취급](#얼굴-데이터-취급)).
- **학습 데이터셋과 산출물(가중치, `runs/`)은 저장소에 커밋하지 않는다.** 공용 서버
  디스크 여유가 약 17~20 GB뿐이라([결정 0028](../docs/architecture/decisions.md#0028--영상-원본을-저장하지-않고-스냅샷만-남긴다))
  로컬에도 오래 남기지 않는다.
- **학습된 가중치를 `worker/inference`가 쓰는 실행 환경까지 전달하는 방식은 아직
  정해지지 않았다.** [모델 파일 취급](#모델-파일-취급)의 미결정 사항이 그대로 적용된다.

## 모델 파일 취급

모델 가중치 파일은 애플리케이션 코드와 분리한다.

- 저장소에 대용량 가중치 파일을 커밋하지 않는다.
- 모델 경로·버전은 환경변수로 주입한다.
- 모델 교체 시 입출력 스키마 변경 여부를 먼저 확인한다.
- **모델 버전을 Face Profile 메타데이터에 기록한다.** 인식 모델을 바꾸면 기존
  embedding을 그대로 쓸 수 없다. 어떤 버전으로 만든 벡터인지 남지 않으면
  재등록 대상을 찾을 수 없다.

배포 시 모델 파일 전달 방식은 **결정 필요**다.
객체 저장소가 MinIO로 확정됐으므로([결정 0004](../docs/architecture/decisions.md#0004--영상과-얼굴-이미지-저장소로-minio-채택))
MinIO에서 내려받는 방식이 후보에 포함된다. 이미지 내 포함, 볼륨 마운트도 여전히 후보다.

## 얼굴 데이터 취급

**얼굴은 그 자체로 개인정보다.** 학생이 미성년자일 수 있다.

- **동의·보관 범위·보존 기간·접근 권한·삭제 절차가 모두 `결정 필요`다.**
  합의 전에 얼굴 데이터를 다루는 기능을 만들지 않는다.
- embedding과 얼굴 이미지를 로그·오류 메시지·테스트 자산에 남기지 않는다.
- **테스트에 실제 사람의 얼굴을 쓰지 않는다.** 합성 이미지나 고정 벡터를 쓴다.
- **식별하지 못한 것을 억지로 식별하지 않는다.** 신뢰도가 기준 미만이면 신원 필드를
  비우고 가장 가까운 학생을 고르지 않는다. `UNKNOWN`은 FastAPI가 판정한다. 오인식은
  다른 학생의 정보를 노출하는 사고다.

## 예상 기술

| 항목 | 상태 | 비고 |
| --- | --- | --- |
| 언어 | Python | |
| 모델 | 위 [모델 선정](#모델-선정) 표 참고 | 대부분 `결정 필요` |
| 실행 환경 | CPU / GPU 모두 고려 | Jetson 실행 여부 **결정 필요** |
| 호출 방식 | 결정 필요 | 라이브러리 import / 별도 프로세스 후보 |

## 다른 서비스와의 관계

- [`worker/inference`](../worker/inference/README.md): 프레임을 넘겨 이 디렉터리의
  추론을 호출한다. 호출 방식은 **결정 필요**.
- `webapps/fastapi`: 추론 결과의 소비자이자 상태 판정 주체다. 얼굴 등록 시 embedding
  생성도 여기를 통한다. 결과 스키마는 fastapi와 합의한 뒤 변경한다.
- 브라우저: 직접 호출하지 않는다. `fastapi`를 통해서만 결과에 접근한다.
- `monitoring`: 추론 지연·처리량·식별 성공률 지표를 노출한다.
  지표 이름은 `classroom_monitoring_` 접두사를 사용한다.

## 향후 구현 시 필요한 환경변수

값의 취급과 명명 규칙은 [환경변수 규칙](../docs/conventions/environment-convention.md)을 따른다.

| 이름 | 용도 | 비고 |
| --- | --- | --- |
| `MODEL_PATH` | 사람 탐지 모델 가중치 경로 | 코드에 하드코딩 금지 |
| `FACE_DETECTION_MODEL_PATH` | 얼굴 탐지 모델 경로 | 모델 확정 후 |
| `FACE_RECOGNITION_MODEL_PATH` | 얼굴 인식 모델 경로 | 모델 확정 후 |
| `MODEL_VERSION` | 모델 버전 식별자 | 결과 추적과 embedding 호환성 판단용 |
| `DEVICE` | 실행 장치 | `cpu` / `cuda` |
| `CONFIDENCE_THRESHOLD` | 탐지 신뢰도 임계값 | 기본값 허용, 환경별 조정 가능 |
| `MAX_BATCH_SIZE` | 배치 크기 | 배치 처리 도입 시 |

학생 식별 신뢰도 임계값(`IDENTITY_CONFIDENCE_THRESHOLD`)은 판정 기준값이므로
`webapps/fastapi` 쪽 설정이다. 두 값을 하나로 합치지 않는다.

## 지표 노출

`METRICS_ENABLED`가 켜져 있으면(기본) 앱과 같은 포트(8100)에 `/metrics`를 연다.
**끄면 라우트 자체를 만들지 않는다** — 404를 돌려주는 경로를 남기면 "지표가 있는데
지금 실패한 것"과 "이 배포에는 없는 것"이 구분되지 않는다. 값은 기동 시점에 읽는다.

```bash
curl -s http://127.0.0.1:8100/metrics | grep classroom_monitoring_
```

재는 것은 세 가지다.

- **구간별 지연**(`detect`·`pose`·`quality`·`total`) — `/internal/face-analysis`는 등록
  중 프레임마다 불리는 실시간 경로다. 느려지면 사용자는 가이드가 반응하지 않는다고
  느끼는데, 느린 쪽이 SCRFD인지 MediaPipe인지 로그로는 알 수 없다.
- **요청 결과**(`ok`·`no_face`·`bad_image`·`missing_session`·`error`) — `no_face`는
  실패가 아니라 정상적인 결과라 나머지와 섞지 않는다.
- **남아 있는 세션 수** — 등록 세션 이력은 `DELETE .../sessions/{id}`가 와야 비워진다.
  브라우저가 화면을 그냥 닫으면 항목이 남고, 이 값이 단조 증가하면 그 상태다.

**`enrollment_id`와 얼굴에서 나온 수치(신뢰도·blur·yaw)는 지표로 내보내지 않는다.**
앞은 값이 무한히 늘어나면서 개인을 가리키고, 뒤는 개인의 촬영 상태가 집계 밖으로
나가는 일이다. 정의는 [`metrics.py`](./metrics.py)에 있고, 무엇을 왜 재는지와 PromQL
예시는
[`monitoring/internal/README.md`](../monitoring/internal/README.md#지금-노출하는-지표--deeplearning)가
정본이다.

`metrics.py`는 **`metrics`라는 이름 하나로만 import한다.** 컨테이너는
`uvicorn app:app`으로 뜨고 테스트는 `deeplearning.app`으로 부르는데, 두 이름을 섞으면
모듈이 두 번 로드되어 같은 지표를 두 번 등록하려다 죽는다.

## 테스트 전략

- 전처리·후처리 함수는 모델 없이 단위 테스트한다.
- 결과 스키마 변환은 고정된 샘플 입력으로 검증한다.
- 갤러리 대조는 고정 벡터로 검증한다. 실제 얼굴을 쓰지 않는다.
- 모델 자체를 요구하는 테스트는 별도로 표시해 기본 테스트에서 분리한다.
- 성능 측정은 테스트가 아닌 별도 벤치마크로 다루고, 측정하지 않은 수치를 문서화하지 않는다.

```bash
# 저장소 최상위에서. mediapipe·insightface가 설치돼 있어야 한다.
python -m pytest deeplearning/tests -q

# 모델 의존성 없이 지표 정의만 확인할 때
python -m pytest deeplearning/tests/test_metrics.py -q
```

**`app.py`를 import하는 테스트는 mediapipe·insightface를 요구한다.** `test_metrics.py`는
`metrics.py`만 보므로 그 의존 없이 돌고, `test_metrics_endpoints.py`는 없으면 스스로
건너뛴다. 다만 기존 `test_face_guide.py`·`test_face_quality.py`는 수집 단계에서 멈추므로
의존성이 없는 환경에서는 디렉터리 전체가 아니라 파일을 지정해 실행한다.

## SCRFD local 실행

사전학습 모델은 비상업적 연구 용도로만 사용한다. 모델 파일은 Git에 포함하지 않고
`FACE_DETECTION_MODEL_PATH`로 주입한다.

```powershell
conda activate smart_monitoring
cd deeplearning
$env:FACE_DETECTION_MODEL_PATH=(Resolve-Path '.models\scrfd\scrfd_10g_bnkps.onnx')
$env:FACE_LANDMARKER_MODEL_PATH=(Resolve-Path '.models\mediapipe\face_landmarker.task')
python -m uvicorn app:app --port 8100
```

`POST /internal/face-analysis`는 JPEG 바이트와 `X-Face-Enrollment-ID`를 받아 얼굴 수,
bbox 기반 크기 비율, 검출 신뢰도, 안내 타원 포함 여부, MediaPipe yaw·pitch·roll과
얼굴 crop의 흐림·밝기, 프레임 간 각속도·중복 점수를 반환한다. 중복은 같은 세션의 최근
120개 특징 중 유사 자세와 비교하고, 비교 상태는 완료·취소 시 삭제한다. 얼굴 이미지와
비교 지문은 응답·로그에 포함하지 않으며 저장 여부와 파일명은 FastAPI가 결정한다.

## 관련 문서

- [AI 에이전트 규칙](../docs/agents/ai-agent.md)
- [worker README](../worker/README.md) — 프레임 공급과 실행 단계
- [학생 모니터링 MVP 명세](../docs/specs/student-monitoring-mvp.md) — 탐지 결과가 어떻게 쓰이는지
- [training README](./training/README.md) — 모델 학습용 Jupyter 노트북 사용법
- `add-monitoring-metric` 스킬
- [아키텍처](../docs/architecture/README.md)
