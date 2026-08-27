# deeplearning/training

공용 GPU 서버에서 **사람 탐지(Person Detection) 모델**을 학습하는 절차다. 탐색 작업은
Jupyter 노트북으로 할 수 있고, 확정된 전처리·검수·학습 계약은 `auto_labeling`의 Python
CLI로 반복 실행할 수 있다.

> **범위**: 사람 탐지 모델의 수동 fine-tuning까지만 다룬다. 얼굴 탐지·인식 모델 학습,
> 자동 재학습 파이프라인, 데이터셋 라벨링 도구는 범위 밖이다
> ([결정 0012](../../docs/architecture/decisions.md#0012--deeplearning에-모델-학습용-jupyter-노트북-도구를-둔다)).
> 얼굴 데이터는 개인정보 합의 전까지 이 노트북에서도 다루지 않는다.

## 준비물

- 공용 GPU 서버 SSH 접속 정보(개인 계정). **저장소에 접속 정보를 남기지 않는다** —
  팀 내부 채널로 개별 공유한다.
- YOLO 포맷으로 라벨링된 학습 데이터셋(`images/`, `labels/`, `data.yaml`).
  데이터셋을 어떻게 확보·라벨링할지는 아직 `결정 필요`다 — 이 노트북은 이미 만들어진
  데이터셋이 있다고 가정한다.

## Python 파이프라인 경계

원본 강의실 영상은 GPU 서버로 보내지 않는다. 승인된 학생 프레임은 승인 범위와 보존
기한을 manifest로 검증한 뒤에만 원본 프레임 학습 export에 포함할 수 있다. 다음 두 실행
경계를 유지한다.

1. 로컬 PC: 세션 분리 → 프레임 추출 → 자동 라벨링 → 사람 검수 → 비식별 또는 승인된 원본 프레임 ZIP 생성
2. GPU 서버: ZIP·승인/전처리 계약·SHA-256 검증 → 1 epoch smoke → YOLO11n 또는 YOLO26n 정식 학습 → 결과 ZIP 생성

이 CLI는 운영자가 직접 실행하는 오프라인 작업이다. 예약 실행이나 자동 재학습 서비스가
아니며, 기존 서버 Docker 구성과도 연결되지 않는다.

## GPU 서버 Python CLI 사용법

저장소와 비식별 ZIP을 서버의 작업 허용 경로에 준비한 뒤
[`gpu_server_training.example.yml`](./auto_labeling/config/gpu_server_training.example.yml)을
복사해 `server_root`, 절대 경로와 SHA-256을 채운다. SSH 접속 정보와 비밀번호는 설정
파일에 넣지 않는다. N1 v008을 같은 설정으로 재현할 때는 실제 해시가 채워진
[`gpu_server_training.v008.yml`](./auto_labeling/config/gpu_server_training.v008.yml)을
`data/server-transfer-v008/gpu-server-training.yml`로 복사하고 경로 자리표시자를 승인된
서버 작업 경로로 바꾼다. 이 로컬 설정은 `data/` 아래라 커밋되지 않는다.

서버로 `deeplearning/training` 전체를 복사하면 원본 데이터·review·과거 run이 함께 갈 수
있으므로 금지한다. 대신 비식별 ZIP·기준 모델의 해시를 검증하고 실행 코드만 묶는다.

```powershell
python -m auto_labeling.server_bundle `
  --config data/server-transfer-v008/gpu-server-training.yml `
  --dataset-archive data/person-pipeline/colab-export-v008-true-empty-negative17.zip `
  --base-model data/auto-labeling/models/yolo11n.pt `
  --output-dir data/server-transfer-v008 `
  --bundle-id v008
```

출력되는 코드 ZIP에는 `auto_labeling` 런타임, 서버 전용 requirements, 실제 학습 YAML만
포함된다. 원본 영상·review 프레임·가중치는 포함하지 않는다. 함께 생성되는 transfer
영수증의 세 항목(코드 ZIP, 비식별 데이터 ZIP, 기준 모델)만 승인 후 서버로 보낸다.

```bash
cd classroom_monitoring/deeplearning/training
python3 -B -m auto_labeling.server_preflight --config /absolute/path/to/training.yml
```

이 부트스트랩 검사는 Torch·Ultralytics·OpenCV를 import하지 않으므로 패키지가 아직 없는
호스트에서도 GPU 할당, 여유 메모리, ZIP SHA-256과 경로 권한을 확인한다. 누락 패키지를
준비한 뒤 전체 계약 검사를 실행한다.

```bash
python -B -m auto_labeling pipeline-train-check --config /absolute/path/to/training.yml
```

`status`가 `ready-for-training`인지 확인한 다음에만 학습한다. 사전점검은 데이터 압축을
풀거나 학습 출력 폴더를 만들지 않으며 `artifact_writes_performed: false`를 반환한다.
다만 ZIP 내부 개인정보 계약의 전체 파일 검증은 압축 해제 후에만 가능하므로 실제 학습
진입점에서 다시 검사한다.

`device: auto`는 `allowed_cuda_devices` 안에서 여유 메모리가 가장 큰 장치를 고른다.
2026-08-22 읽기 전용 서버 조사에서는 프로젝트에 할당된 장치가 GPU 1번이므로 서버 예시는
`allowed_cuda_devices: [1]`로 고정했다. 다른 GPU가 비어 보여도 허용 목록 밖이면 사용하지
않는다. 기본 `minimum_cuda_free_gib: 8`을 만족하지 못하면 시작하지 않는다. 이는 GPU 예약
기능이 아니므로 사전점검과 실제 학습 사이에 다른 작업이 시작될 수 있다. 공용 서버에서는
학습 직전에 팀 채널에서 GPU 사용 시간을 확인한다.

### 2026-08-22 서버 읽기 전용 조사 결과

- Ubuntu 24.04.4, Python 3.12.3, NVIDIA L40S 4장, 드라이버 595.84
- 프로젝트의 기존 inference 컨테이너는 GPU 1번만 할당받고 모델 마운트는 읽기 전용이다.
- 호스트 Python에는 Torch·Ultralytics·NumPy·OpenCV가 없다. `nvcc`도 없지만 사전 빌드
  CUDA torch wheel을 쓰므로 학습 필수 조건은 아니다.
- 기존 Docker와 컨테이너는 학습 환경으로 재사용하거나 변경하지 않는다. 별도 승인을 받은
  사용자 작업 폴더의 Python 3.12 가상환경에서 실행한다.

서버에는 `python3-venv`와 `ensurepip`가 없으므로 `python3 -m venv`로 만들지 않는다.
사용자 작업 폴더 생성과 패키지 설치가 승인된 뒤에는 `virtualenv` 자체도 승인된 폴더에만
설치하고, 저장소의 worker GPU 이미지와 같은 CUDA 12.6 wheel 계열을 사용한다. 시스템
Python이나 기존 컨테이너에는 설치하지 않는다.

```bash
python3 -m pip install --target /absolute/approved/path/bootstrap \
  'virtualenv>=20,<21'
PYTHONPATH=/absolute/approved/path/bootstrap \
  python3 -m virtualenv /absolute/approved/path/.venv
source /absolute/approved/path/.venv/bin/activate
python -m pip install --index-url https://download.pytorch.org/whl/cu126 torch torchvision
python -m pip install -r requirements-server.txt
python -m pip check
```

```bash
python -m auto_labeling pipeline-train --config /absolute/path/to/training.yml
```

학습 명령이 쓰는 위치는 설정의 `extract_root`와 `output_root`뿐이다. `mode: smoke-full`은
1 epoch smoke가 끝난 뒤 정식 학습을 이어가며, 완료 시 `best.pt`, 학습 영수증, validation
F1 threshold, `model_contract.json`, 결과 ZIP과 각 SHA-256 영수증을 남긴다. 모델 계약에는
가중치 해시, 대상 클래스, 학습 image size와 학습 데이터의 전처리 계약이 들어간다. 서버가 오프라인이면
`base_model`에 미리 준비한 `yolo11n.pt`의 절대 경로를, `base_model_sha256`에 실제 해시를
지정한다.

### N1 원본 프레임 교체 계약

기존 N1 데이터셋은 `uniform-full-frame-pixelation-v1`, block size 8로 학습돼 원본 프레임을
직접 받는 worker와 호환되지 않는다. 교체 모델은 같은 승인 데이터·라벨·train/val 분할을
유지하되 이미지 바이트만 원본으로 내보낸 `original-frame-v1` 계약으로 학습한다. 원본
export는 모든 항목이 학생 데이터, 사람 탐지 학습 범위, 사람 검수 승인, 유효한 보존 기한을
가져야 하며 하나라도 빠지면 생성하지 않는다.

배포할 때 `best.pt`와 같은 결과의 `model_contract.json`을 함께 복사한다. dev/prod worker와
GPU 배포 사전점검은 모델 해시·클래스·image size·전처리 계약을 확인하고, 값이 다르거나
`inference_preprocessing_required: true`인 기존 픽셀화 모델이면 기동하지 않는다.

2026-08-25에 승인된 `person-v0002` 320장(train 240 / val 80), seed 42, 640px로 50 epoch
학습한 원본 v005 best 가중치는 SHA-256
`dd658747ab201211047b57cb8c30e54a8cc59a4769ccd5fc031ae0b6b1703ef7`이다. 고정 validation
80장·1,045 instances에서 precision 0.936, recall 0.918, mAP50 0.957, mAP50-95 0.515였고,
confidence sweep의 최고 F1 0.927은 confidence 0.30에서 나왔다.

### 원본 CCTV 500장 YOLO26n 준비 계약

[`person_500_yolo26n.local.example.yml`](./auto_labeling/config/person_500_yolo26n.local.example.yml)은
픽셀화하지 않은 원본 프레임 500장을 `train 350 / val 75 / 고정 test 75`로 준비한다.
`train`과 `val`만 학습 ZIP에 들어가고 `test`는 별도 디렉터리에서 사람이 검수한 뒤
SHA-256으로 동결한다. 여러 모델 버전 비교에서는 동결된 test를 복사·재선정하지 않고 같은
영수증 해시를 사용한다.

분할 우선순위는 이미지 장수보다 촬영 세션 격리다. 먼저 세션 전체를 dataset/train,
dataset/val, benchmark/test, excluded 중 하나로 정한 뒤, 각 split 안에서 영상별로 균등
배분하고 시간축 등간격으로 프레임을 선택한다. 모든 원본의 해시, 해상도, FPS, 첫 프레임
디코딩을 검사하고, 추출 시 보고된 전체 프레임 수보다 0.1% 이상 일찍 디코더가 끝나면
실패한다. 선택 프레임에는 손상·단색·검은 화면·색상 붕괴 품질 검사와 정확한 이미지 해시
중복 제거를 적용한다.

이번 로컬 원본은 37개 MP4, 8개 촬영 세션이며 해상도는 모두 1280×1944다. 명목
프레임레이트와 실제 평균 FPS가 다른 가변/드롭 프레임 영상이 있으므로 영상별 FPS를 읽어
2초 간격을 계산한다. 자동 라벨링과 학습은 원본 해상도를 보존하고 `imgsz=1280`을
명시한다. 이는 모델 텐서를 정확히 1280×1944로 고정한다는 뜻이 아니라 Ultralytics의
비율 유지 letterbox 입력 상한을 1280으로 맞춘다는 뜻이다.

이 프로필은 `require_session_approval_metadata: false`로 설정해 로컬 프레임 추출과
YOLO 자동 라벨링까지는 `approval_reference`, `retention_expires_at`, `subject_category`를
요구하지 않는다. 빈 값을 승인된 값으로 바꾸거나 추정하지 않으며, 외부 반출·학습 export의
개인정보 검증은 별도 단계에 그대로 유지한다.

```powershell
cd deeplearning/training
python -m auto_labeling pipeline-local `
  --config data/person-pipeline-workflows/person-original-500-v001/local-pipeline.yml
```

상태가 `waiting-for-human-review`가 되면 학습용 `review-main` 425장과 `05-fixed-test`
75장의 후보 bbox를 모두 검수한다. 빈 장면은 빈 `.txt`가 정답이고, 겹친 사람이 실제로
여럿이면 각 사람 bbox를 유지하며, 같은 사람을 중복으로 감싼 bbox는 하나만 남긴다.
검수 완료 후 `reviewer_id`, `labelimg_executable`, `labelimg_smoke_confirmed`를 채우고
`--complete-review`로 재실행한다. 이때 test와 train/val 간 exact·근접 중복 검사를 통과해야
test 동결 영수증과 학습 ZIP이 생성된다.

학습은
[`person_500_yolo26n.training.example.yml`](./auto_labeling/config/person_500_yolo26n.training.example.yml)을
복사해 실제 ZIP 해시와 서버 경로를 채운다. 기본안은 YOLO26n, seed 42, `imgsz=1280`,
100 epochs, patience 20이며 먼저 1 epoch smoke가 성공해야 정식 학습으로 넘어간다.

## 얼굴 식별 모델 평가와 임계값 산출

`face_identification_eval.py`는 등록 학생(known)과 미등록 인원(unknown)을 validation/test로
고정 분리해 ArcFace 또는 AdaFace를 같은 조건으로 평가한다. validation으로 유사도와
1·2위 margin 임계값을 고른다. known validation 안의 같은 학생·다른 학생 이미지 쌍으로
얼굴 track 연결 유사도도 고르며, 다른 학생 쌍의 허용 오연결률은
`FACE_EVAL_TRACK_TARGET_FALSE_ASSOCIATION`으로 제한한다. test 결과 CSV와 런타임용
`thresholds.json`에는 세 임계값과 목표 오연결률이 함께 들어간다.
test 결과를 보고 다시 임계값을 고르지 않는다.

```bash
cd <저장소 루트>
python -m pip install -r deeplearning/training/requirements-face-eval.txt
python -m deeplearning.training.face_identification_eval
```

`training/requirements.txt`는 사람 탐지 학습·노트북용이며 얼굴 평가 런타임 의존성을
모두 설치하지 않는다. 얼굴 임계값만 생성할 때는 위 `requirements-face-eval.txt`를 쓴다.

필수 디렉터리와 모델 경로는 [`.env.example`](./.env.example)의 `FACE_EVAL_*`,
`FACE_*_MODEL_PATH`를 따른다. known 디렉터리는 `<student_id>/*.jpg`, unknown 디렉터리는
하위의 이미지 파일 구조다. 실제 얼굴·가중치·CSV·임계값 산출물은 커밋하지 않는다.
MongoDB를 건드리지 않는 dry-run은 `FACE_EVAL_GALLERY_SOURCE=directory`와
`FACE_EVAL_GALLERY_DIR=<student_id별 등록 이미지 루트>`를 쓴다.

MongoDB에 저장된 등록 embedding을 갤러리로 쓰려면 `FACE_EVAL_GALLERY_SOURCE=mongodb`,
`MONGODB_URI`, `MONGODB_DATABASE`, `FACE_EMBEDDING_COLLECTION`을 설정한다. **MongoDB
평가도 실시간 런타임과 같은 규칙으로 활성·얼굴 등록 학생의 embedding만 사용한다.**
embedding만으로 임계값을 만들 수는 없다. 오인식률을 측정할 별도의 known/unknown
validation과 test 이미지가 네 디렉터리에 모두 있어야 한다. 로컬 Windows에서 실행하면
`FACE_EVAL_*_DIR`와 모델 경로도 Windows 절대경로여야 하며, `/home/...` 경로는 GPU 서버에서
실행할 때만 유효하다.

생성된 임계값 파일에는 모델명·모델 버전·전처리 버전이 함께 들어간다. 실시간 서비스의
`FACE_IDENTITY_THRESHOLD_FILE`로 연결하며, 현재 런타임 메타데이터와 하나라도 다르면
기동을 거부한다. `similarity_threshold`, `margin_threshold`,
`track_similarity_threshold` 중 하나라도 없거나 track 목표 오연결률이 배포 기준보다
느슨하면 배포 검증도 실패한다. 실측 데이터가 없으면 임의 임계값으로 학생 이름을 붙이지
않는다.

AdaFace ONNX는 `prepare_adaface_model.py`, person re-ID 가중치는
`prepare_person_reid.py`로 준비한다. `cross_camera_demo.py`와 tracking 노트북은 카메라 간
인계 실험용이며 운영 파이프라인에는 연결돼 있지 않다.

## Jupyter 사용법

1. **SSH로 공용 서버에 접속한다.**

   ```bash
   ssh <계정>@<공용-서버-주소>
   ```

2. **저장소를 받고 이 디렉터리로 이동한다.**

   ```bash
   git clone <저장소-주소>
   cd classroom_monitoring/deeplearning/training
   ```

   이미 clone된 저장소가 있다면 `git pull`로 최신화한다.

3. **가상환경을 만들고 패키지를 설치한다.**

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

   서버에 CUDA용 torch가 이미 설치돼 있다면 `pip install ultralytics --no-deps`처럼
   torch 재설치를 피하는 방법도 있다. `requirements.txt`의 주석을 참고한다.

4. **설정을 채운다.**

   ```bash
   cp .env.example .env
   ```

   `DATASET_DIR`, `DEVICE` 등을 실제 값으로 채운다. 값의 의미는 `.env.example`의
   주석에 있다.

5. **학습 데이터셋을 서버로 옮긴다.** 로컬 PC에서:

   ```bash
   rsync -avz ./내-데이터셋/ <계정>@<공용-서버-주소>:<DATASET_DIR>/
   ```

   `data.yaml`의 `train`·`val` 경로가 실제 이미지 위치를 가리키는지 확인한다.

6. **Jupyter Lab을 서버에서 실행한다.**

   ```bash
   jupyter lab --no-browser --port=8888
   ```

7. **로컬 PC에서 SSH 포트포워딩으로 접속한다.** 새 터미널에서:

   ```bash
   ssh -L 8888:localhost:8888 <계정>@<공용-서버-주소>
   ```

   브라우저에서 서버가 출력한 `http://localhost:8888/lab?token=...` 주소를 연다.

8. **`notebooks/01_person_detection_training.ipynb`을 열고 위에서부터 셀을 하나씩
   순서대로 실행한다.** 각 셀은 이전 셀의 결과에 의존한다.

## 주의할 점

- **공용 GPU를 여러 명이 나눠 쓴다.** 학습을 시작하기 전에 노트북의 GPU 확인 셀로
  `nvidia-smi` 결과를 보고, 이미 쓰는 사람이 있으면 팀 채널에 먼저 확인한다.
- **디스크 여유가 크지 않다.** 공용 서버의 가용 용량이 약 17~20 GB뿐이다
  ([결정 0011](../../docs/architecture/decisions.md#0011--영상-원본을-저장하지-않고-스냅샷만-남긴다)).
  학습이 끝나면 노트북의 정리 셀로 데이터셋과 `runs/`를 지운다. 큰 데이터셋을
  서버에 영구히 두지 않는다.
- **데이터셋·가중치·`runs/`는 커밋하지 않는다.** 저장소의 `.gitignore`가 이 디렉터리
  아래 `data/`, `runs/`, `weights/`와 모델 가중치 확장자를 막는다.
- **학습된 가중치를 운영 추론 환경(`worker/inference`)까지 어떻게 전달할지는 아직
  정해지지 않았다.** 지금은 각자 로컬에 내려받아 필요한 사람과 직접 공유한다.

## 파일 구성

```text
training/
├── README.md          이 문서
├── requirements.txt    학습 노트북 의존성
├── .env.example         설정값 이름(값은 비움)
├── face_identification_eval.py   고정 split 얼굴 식별 평가·임계값 생성
├── adaface_recognizer.py         AdaFace ONNX 런타임 어댑터
├── cross_camera_demo.py          카메라 간 인계 실험 진입점
└── notebooks/
    ├── 01_person_detection_training*.ipynb   사람 탐지 모델 학습·평가
    └── 02_person_detection_tracking*.ipynb   단일·교차 카메라 tracking 실험
```

## 남은 일

- 학습 데이터셋 확보·라벨링 정책 확정.
- 학습 가중치를 `worker/inference`가 쓰는 실행 환경까지 전달하는 방식 확정
  (MinIO가 후보다).
- 얼굴 탐지·인식 모델 학습 노트북 추가 — 개인정보 합의 이후.
- 공용 서버에서 GPU 동시 사용을 조율하는 절차(예약, 알림) 마련.

## 관련 문서

- [deeplearning README](../README.md)
- [결정 0029](../../docs/architecture/decisions.md#0029--deeplearning에-모델-학습용-jupyter-노트북-도구를-둔다)
- [결정 0011](../../docs/architecture/decisions.md#0011--영상-원본을-저장하지-않고-스냅샷만-남긴다) — 공용 서버 용량 제약의 근거
- [환경변수 규칙](../../docs/conventions/environment-convention.md)
