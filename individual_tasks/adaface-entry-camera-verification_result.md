# 무엇을 했나

입구 카메라 영상에서 학생을 AdaFace로 식별하는 운영 경로를 실제 카메라로 검증했다. 기존
ArcFace/AdaFace 선택 구조와 CCTV ByteTrack 경로는 변경하지 않고, USB 카메라 또는 RTSP를
입력으로 받아 같은 얼굴 식별 런타임을 시험하는
`deeplearning/training/webcam_face_identification.py`를 추가했다. 자동 검증은
`deeplearning/tests/test_webcam_face_identification.py`에 추가했다.

검증 도구는 두 방식으로 동작한다.

- 로컬 모드: 검증 PC에서 SCRFD, AdaFace, MongoDB 갤러리 비교를 모두 수행한다.
- 서버 모드: 프레임을 JPEG로 만들어 기존 deeplearning 서버의
  `POST /internal/face-identifications`로 전송한다.

학생 이름이나 ID는 코드에 하드코딩하지 않았다. 이름은 인식된 `student_id`에 대응하는
`face_embeddings_adaface.student_name`에서 가져온다. 원본 프레임이나 새 임베딩도 디스크에
저장하지 않는다.

# 무엇이 바뀌었나

## 처리 로직

```text
USB 카메라 또는 RTSP
  → OpenCV 프레임 수신
  → SCRFD 10G 얼굴 검출과 5점 landmark 정렬
  → AdaFace IR-50 512차원 임베딩 생성 및 L2 정규화
  → face_embeddings_adaface 전체 갤러리와 cosine similarity 비교
  → top-1 similarity와 top-1/top-2 margin 검사
  → tracker에서 동일 얼굴 반복 관측
  → REGISTERED / UNCERTAIN / UNKNOWN 표시
```

`REGISTERED`는 가장 가까운 벡터를 무조건 표시하는 상태가 아니다. 얼굴 크기와 blur 등 품질,
similarity, top-1/top-2 margin, track similarity, 최소 반복 관측을 모두 통과해야 한다.

## 현장에서 조정한 값

| 항목 | 기존 | 현재 | 목적 |
| --- | ---: | ---: | --- |
| SCRFD `det_thresh` | 0.6 | 0.5 | 먼 거리 얼굴을 더 일찍 검출 |
| runtime detection threshold | 0.6 | 0.5 | 검출기와 runtime 기준 일치 |
| 최소 얼굴 크기 | 40px | 30px | 작은 얼굴을 검출 후보로 허용 |
| 기본 추론 간격 | 2프레임 | 1프레임 | 매 프레임 추론 |
| 최소 반복 관측 | 4회 | 3회 | 정확도 기준을 유지하며 확정 단축 |

다음 정확도 보호 설정은 유지했다.

- identity detection confidence 0.6
- preferred face size 112px
- minimum/preferred blur score 20/100
- uncertain quality threshold 0.45
- flip TTA 사용, similarity/margin band 0.08/0.06
- tracker history 12, stale frames 30

현장 기능 확인에 쓴 시작값은 similarity 0.35, margin 0.05, track similarity 0.30이다.
충분한 known/unknown 표본으로 FAR/FRR을 측정해 확정한 운영값은 아니다.

## 필요한 파일

- `deeplearning/training/webcam_face_identification.py`
- `deeplearning/tests/test_webcam_face_identification.py`
- 기존 `deeplearning/face_identification.py`, `face_recognizer.py`,
  `adaface_recognizer.py`
- `deeplearning/.models/scrfd/scrfd_10g_bnkps.onnx` (16,944,462 bytes)
- `deeplearning/.models/adaface/adaface_ir50.onnx` (174,391,973 bytes)
- 로컬 DB 연결용 ignored 파일 `deeplearning/training/.env.face`

ONNX 모델은 Git에 포함하지 않고 대상 환경에 별도로 배치한다. AdaFace 모델은 필요하면
`python -m deeplearning.training.prepare_adaface_model`로 준비한다.

## 필요한 실행 환경

검증 환경은 Python 3.12.13, OpenCV 5.0.0, NumPy 2.5.2, Requests 2.34.2,
PyMongo 4.17.0, ONNX Runtime 1.28.0, InsightFace 1.0.1이다. `python-dotenv`도 필요하다.
ONNX Runtime은 CUDA 실패 시 CPU로 재시도하므로 GPU 없이 기능 테스트가 가능하지만 느리다.

```powershell
conda create -n smart_monitoring python=3.12 -y
conda activate smart_monitoring
python -m pip install -r deeplearning/requirements.txt
python -m pip install requests python-dotenv pytest ruff
```

서버 requirements의 `opencv-python-headless`는 화면 창을 지원하지 않는다. 현장 검증 PC는
다음처럼 GUI 패키지를 사용한다. 운영 서버는 headless를 유지해도 된다.

```powershell
python -m pip uninstall -y opencv-python-headless
python -m pip install opencv-python
```

VS Code interpreter 변경이 기존 터미널에 반영되지 않을 수 있어 실행은
`conda run -n smart_monitoring`을 권장한다.

## 필요한 DB 환경

- DB: `classroom_monitoring`
- 컬렉션: `face_embeddings_adaface`
- 필수 데이터: `student_id`, `student_name`, 512차원 `vector`, 차원·정규화·모델·버전·전처리 metadata
- 학생 문서와 임베딩 문서의 `student_id` 연결

로컬 `.env.face`에는 실제 값을 Git에 넣지 않고 다음 키만 구성한다.

```dotenv
MONGODB_URI=<MongoDB 연결 URI>
MONGODB_DATABASE=classroom_monitoring
```

## 카메라와 네트워크

- USB 직접 연결은 Windows DirectShow 장치 번호를 preview로 확인한다. 현장 테스트는 1번이었다.
- 라즈베리파이는 ABKO 카메라 `/dev/video0`을 FFmpeg로 읽어 systemd
  `entrance-publisher.service`에서 GPU MediaMTX `camera-01` 경로로 publish한다.
- 확인 당시 RTSP는 H.264, 640x480, 15fps였다.
- 노트북·라즈베리파이·GPU 서버는 Tailscale로 통신하며 실제 주소와 RTSP 인증정보는
  배포 env에만 둔다.
- 라즈베리파이 로그에는 frame duplication과 일부 V4L2 corrupted buffer가 있었다.
  RTSP 지연 개선 시 입력 FPS, MJPEG 지원 여부, FFmpeg timestamp와 buffering을 점검한다.

## 실행 방법

USB 영상 확인:

```powershell
conda run -n smart_monitoring python -m deeplearning.training.webcam_face_identification `
  --camera-check-only --camera-index 1
```

USB 로컬 AdaFace 식별:

```powershell
conda run -n smart_monitoring python -m deeplearning.training.webcam_face_identification `
  --local --camera-index 1 --database-name classroom_monitoring `
  --similarity-threshold 0.35 --margin-threshold 0.05 `
  --track-similarity-threshold 0.30
```

RTSP는 `--camera-index 1` 대신 `--rtsp-url "<입구 카메라 RTSP URL>"`을 사용한다.
운영 deeplearning 서버를 호출할 때는 `--local`과 threshold 옵션을 제거하고
`--url "http://<서버>:<포트>" --camera-id camera-01`을 사용한다. 종료는 `q` 또는 `Esc`다.

## 운영 환경으로 옮길 설정

검증 스크립트로 기존 worker를 교체하지 않는다. 검증된 모델 선택, 갤러리, threshold와
카메라 source를 운영 deeplearning/worker env에 옮긴다.

```dotenv
FACE_RECOGNIZER=adaface
FACE_RECOGNITION_MODEL_PATH=<컨테이너 내부 AdaFace ONNX 경로>
FACE_RECOGNITION_MODEL_VERSION=cvlface-adaface-ir50-webface4m-fe7718c6
FACE_EMBEDDING_COLLECTION=face_embeddings_adaface
FACE_IDENTIFICATION_ENABLED=true
FACE_GALLERY_DATABASE_URL=<MongoDB URI>
FACE_GALLERY_DATABASE_NAME=classroom_monitoring
FACE_IDENTITY_SIMILARITY_THRESHOLD=<평가 확정값>
FACE_IDENTITY_MARGIN_THRESHOLD=<평가 확정값>
FACE_IDENTITY_TRACK_SIMILARITY_THRESHOLD=<평가 확정값>
FACE_DETECTION_THRESHOLD=0.5
FACE_IDENTITY_MIN_DETECTION_CONFIDENCE=0.6
FACE_MINIMUM_SIZE=30
FACE_IDENTITY_HISTORY_SIZE=12
FACE_IDENTITY_MINIMUM_OBSERVATIONS=3
FACE_IDENTITY_TRACK_STALE_FRAMES=30
```

```dotenv
STREAM_SOURCES=camera-01=<입구 RTSP URL>,classroom-cctv=<CCTV RTSP URL>
FACE_IDENTITY_CAMERA_IDS=camera-01
PERSON_TRACKING_CAMERA_IDS=classroom-cctv
```

FastAPI와 deeplearning 서버의 `FACE_RECOGNIZER`는 모두 `adaface`여야 한다. 모델 경로는
compose volume으로 마운트된 컨테이너 내부 경로를 사용한다. `camera-01`은 얼굴 식별,
`classroom-cctv`는 사람 탐지·ByteTrack 역할로 유지한다.

# 검증

## 실행한 검증

- 웹캠 검증 테스트: 5 passed
- Ruff: All checks passed
- USB 영상과 SCRFD 얼굴 검출 확인
- AdaFace 임베딩과 MongoDB 갤러리 비교 확인
- 등록 학생에 DB 이름 표시 확인
- 원거리 검출과 3회 관측 확정 속도 개선 확인
- RTSP 포트 도달 및 H.264 640x480 15fps 수신 확인
- 라즈베리파이 publisher 서비스와 GPU MediaMTX publish 구성 확인

## 아직 실행하지 못한 검증

- 충분한 등록자/미등록자를 사용한 FAR·FRR 및 최종 threshold 산출
- 정면·측면·빠른 이동·가림과 여러 명 동시 입장의 정량 정확도
- RTSP 지연과 라즈베리파이 frame 오류 원인 제거
- GPU 서버 CUDA provider와 장시간 처리량
- 입구 얼굴 식별 결과를 CCTV ByteTrack으로 인계하는 전체 통합 경로
- 운영 compose/env 반영과 서비스 재기동

# 이어받을 때 알아야 할 것

1. 모델을 새로 학습한 작업이 아니라 기존 AdaFace 비교 경로를 실제 카메라로 검증하고 반응성을 개선한 작업이다.
2. `0.35 / 0.05 / 0.30`은 시작값이며 운영 정확도 보증값이 아니다.
3. 다음 원거리 개선은 threshold 추가 하향보다 문 영역 ROI crop/확대가 우선이다.
4. AdaFace 실시간 임베딩은 반드시 `face_embeddings_adaface`와 비교하며 ArcFace 벡터와 혼용하지 않는다.
5. `.env.face`는 로컬 검증용이고 운영 Docker env는 별도로 구성한다.
6. RTSP 재생 성공과 얼굴 식별 지연은 별개이므로 인코딩·네트워크·디코딩·추론 시간을 분리해 측정한다.
7. `individual_tasks/`는 기본적으로 Git ignore 대상이라 이 문서는 이번 작업에서 명시적으로 추적해야 한다.
