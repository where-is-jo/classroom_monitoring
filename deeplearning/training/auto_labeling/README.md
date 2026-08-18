# 사람 탐지 데이터셋 반자동 라벨링

**목적**: 승인된 MP4에서 사람 탐지 후보 bbox를 만들고 로컬 labelImg 검수를 거쳐 불변
YOLO 데이터셋을 발행한다.
**대상 독자**: 개발자 PC에서 데이터셋을 준비하는 팀원과 labelImg 검수자.

이 도구는 웹 서비스나 상시 워커가 아닌 오프라인 CLI다.

## 안전 범위

- 입력은 승인된 MP4 파일만 허용한다.
- `subject_category`는 `synthetic` 또는 `consenting-adult`만 허용한다.
- 라벨은 class `0`, `person` bbox 하나다.
- 원본 영상은 읽기만 하고 이동·삭제하지 않는다.
- 프레임·라벨·검수 묶음·데이터셋은 Git 제외 대상인 `training/data/` 아래에 둔다.
- 실제 학생 영상, 얼굴·신원·행동·출결 라벨, 실시간 RTSP, 자동 재학습·배포는 지원하지 않는다.

labelImg는 저장소에 포함하거나 이 도구에서 설치·업데이트하지 않는다. 검수자는 이미 동작을
확인한 로컬 버전을 고정해 사용하며, 완료 영수증에는 실행 파일 SHA-256과 smoke test 확인이
기록된다. YOLO 파일 사용법은 [labelImg 공식 README](https://github.com/HumanSignal/labelImg/blob/master/README.rst)를
기준으로 한다.

## 최초 1회 설정

모델 가중치는 크기가 크고 버전마다 Git 이력을 늘리므로 저장소에 커밋하지 않는다.
다음 PowerShell 블록을 `deeplearning/training`에서 실행하면 Python 3.12 가상환경과
의존성을 준비하고, 공식 `yolov8n.pt`를 Git 제외 경로에 내려받은 뒤 파일 해시와
`.gitignore` 적용을 확인한다. 이미 준비된 항목은 재생성하거나 다시 받지 않는다.

```powershell
$ErrorActionPreference = "Stop"
$trainingRoot = (Get-Location).Path
$python = Join-Path $trainingRoot ".venv\Scripts\python.exe"
$modelDir = Join-Path $trainingRoot "data\auto-labeling\models"
$modelPath = Join-Path $modelDir "yolov8n.pt"
$expectedModelSha256 = "f59b3d833e2ff32e194b5bb8e08d211dc7c5bdf144b90d2c8412c47ccfc83b36"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    py -3.12 -m venv .venv
}

& $python -m pip install --upgrade pip
& $python -m pip install -r requirements.txt
New-Item -ItemType Directory -Force -Path $modelDir | Out-Null

if (-not (Test-Path -LiteralPath $modelPath -PathType Leaf)) {
    Push-Location $modelDir
    try {
        & $python -c "from ultralytics import YOLO; YOLO('yolov8n.pt')"
    }
    finally {
        Pop-Location
    }
}

$actualModelSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $modelPath).Hash.ToLowerInvariant()
if ($actualModelSha256 -ne $expectedModelSha256) {
    throw "yolov8n.pt SHA-256이 검증값과 다릅니다: $actualModelSha256"
}

git check-ignore --quiet -- "data/auto-labeling/models/yolov8n.pt"
if ($LASTEXITCODE -ne 0) {
    throw "모델 파일이 .gitignore 대상이 아닙니다. 커밋하지 말고 설정을 확인하세요."
}

Write-Host "자동 라벨링 실행 환경 준비 완료: $modelPath"
```

labelImg는 이 설정에 포함하지 않는다. 검수 단계에서는 별도로 설치한 실행 파일 경로를
`review-complete --labelimg-executable`에 전달한다.

## 입력 manifest

입력은 JSON 파일이다. 상대 `file_path`는 manifest 파일의 디렉터리를 기준으로 해석한다.

```json
{
  "run_id": "classroom-pilot-001",
  "sources": [
    {
      "source_id": "source-001",
      "file_path": "<승인된-MP4-경로>",
      "approval_reference": "approval-001",
      "consent_scope": "person-detection-training",
      "retention_expires_at": "<timezone이-있는-미래-ISO-8601-시각>",
      "camera_id": "camera-001",
      "session_id": "session-001",
      "captured_at": "<timezone이-있는-촬영-ISO-8601-시각>",
      "subject_category": "consenting-adult"
    }
  ]
}
```

`source_id`, `camera_id`, `session_id`에는 사람 이름이나 학번을 쓰지 않는다. 승인 참조는
외부 승인 기록의 ID이며 승인 문서나 개인정보 자체를 manifest에 복사하지 않는다.

## 실행 순서

`deeplearning/training`에서 실행한다.

```powershell
.\.venv\Scripts\python.exe -m auto_labeling prepare --manifest <manifest.json>
.\.venv\Scripts\python.exe -m auto_labeling prelabel `
  --run-dir <run-dir> `
  --model-path data\auto-labeling\models\yolov8n.pt `
  --device cpu
.\.venv\Scripts\python.exe -m auto_labeling prepare-review --run-dir <run-dir>
```

첫 파일럿과 보정 파일이 없는 실행은 모든 프레임을 검수 폴더에 넣는다. 출력된
`review/<batch-id>/`를 labelImg에서 연다.

1. labelImg 저장 형식을 YOLO로 바꾼다.
2. `predefined_classes.txt`를 클래스 파일로 쓰고 `Open Dir`로 검수 폴더를 연다.
3. 모든 이미지의 기존 bbox를 확인하고 누락 추가, 오탐 삭제, 위치·크기를 수정해 저장한다.
4. 합성 이미지 하나에서 저장한 `.txt`를 닫았다 다시 열어 bbox와 `person` 라벨이 같은지
   확인한다. 이 확인을 하지 않았으면 완료 명령을 실행하지 않는다.

```powershell
python -m auto_labeling review-complete `
  --review-dir <review-dir> `
  --reviewer-id <내부-ID> `
  --labelimg-executable <labelImg.exe-또는-labelImg.py> `
  --confirm-labelimg-smoke

python -m auto_labeling calibrate --run-dir <run-dir> --review-dir <review-dir>
python -m auto_labeling publish --run-dir <run-dir>
python -m auto_labeling validate --dataset-dir <dataset-dir>
```

## 중복 제거와 데이터셋 분할

`publish`는 검수·자동 승인 라벨을 확정한 뒤 중복 프레임을 제거하고 대표 프레임만
데이터셋에 넣는다.

- JPEG SHA-256이 같은 프레임은 카메라와 관계없이 중복으로 판정한다. 같은 이미지의 최종
  라벨이 서로 다르면 어느 쪽도 선택하지 않고 발행을 중단한다.
- 시각적 중복은 같은 `camera_id` 전체 세션에서만 비교한다. DCT pHash Hamming 거리 4
  이하, 64×64 회색조 픽셀 MAE 0.02 이하, 같은 수의 bbox가 모두 IoU 0.9 이상으로
  대응할 때 중복이다.
- 대표 프레임은 사람 검수 완료, Laplacian 선명도, `frame_id` 오름차순 순으로 선택한다.
- 제외된 프레임은 발행 데이터셋에만 복사하지 않는다. run·검수 파일은 그대로 보존한다.
- 대표 프레임을 정한 뒤 `session_id` 단위로 train/val/test를 80/10/10 분할한다. 같은
  세션은 하나의 split에만 들어가며 남은 세션이 10개 미만이면 `pilot`이다.

새 발행 manifest는 schema v2이며 중복 기준과 프레임 수를 기록한다.
`deduplication.jsonl`에는 대표·제외 프레임, 판정 유형, 거리, 선명도와 대표 선정 근거가
남는다. `validate`는 기존 schema v1과 v2를 모두 검사한다.

`calibrate`는 같은 모델·카메라·샘플링 정책에서 3개 세션·500프레임을 전수 검수했을 때만
자동 승인 임계값을 활성화한다. 이후 실행은 보정 파일을 명시한다.

```powershell
python -m auto_labeling prepare-review `
  --run-dir <run-dir> `
  --calibration <calibration.json>
```

고신뢰 표본 검수가 실패하면 해당 완료 영수증은 발행에 사용할 수 없다. 완료 명령이
`<기존-batch-id>-fallback-<해시>` 이름으로 전수 검수 배치를 자동 생성하므로 그 폴더를
다시 검수한다. 별도의 전수 재검수가 필요할 때는 다음 명령을 사용할 수 있다.

```powershell
python -m auto_labeling prepare-review `
  --run-dir <run-dir> `
  --batch-id review-full `
  --force-full
```

## 주요 산출물

```text
data/auto-labeling/
├─ runs/<run-id>/
│  ├─ run.json
│  ├─ frames.jsonl
│  ├─ frames/
│  ├─ candidate-labels/
│  ├─ predictions.jsonl
│  ├─ prelabel.json
│  ├─ calibration.json
│  └─ review/<batch-id>/
└─ datasets/person-vNNNN/
   ├─ images/{train,val,test}/
   ├─ labels/{train,val,test}/
   ├─ data.yaml
   ├─ deduplication.jsonl
   └─ manifest.json
```

검수 완료 뒤 이미지·라벨·클래스 파일이 바뀌면 영수증 해시 검증이 실패한다. 기존
`person-vNNNN`은 덮어쓰지 않으며 같은 run을 같은 내용으로 다시 발행하면 기존 버전을
검증한 뒤 그대로 반환한다.

## 검증

```powershell
python -m pip install -r requirements-test.txt
python -m ruff check auto_labeling
python -m ruff format --check auto_labeling
python -m mypy auto_labeling
python -m pytest -q
```

자동 테스트는 합성 MP4, 고정 bbox와 Ultralytics API 대역만 사용하므로 모델 가중치와
torch를 내려받지 않는다. 실제 yolov8n 가중치 추론과 labelImg GUI smoke test는 로컬
파일럿에서 별도로 수행한다. 확인한 버전·해시·결과는 [V1 검증 기록](./VALIDATION.md)에
개인정보와 로컬 경로 없이 남긴다.

## 관련 문서

- [학습 README](../README.md)
- [V1 검증 기록](./VALIDATION.md)
- [환경변수 규칙](../../../docs/conventions/environment-convention.md)
- [AI 에이전트 규칙](../../../docs/agents/ai-agent.md)
