import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPOSITORY_ROOT / ".docker" / "scripts"))
WORKFLOW_PATH = (
    REPOSITORY_ROOT / ".github" / "workflows" / "provision-adaface-model.yml"
)
REQUIREMENTS_PATH = (
    REPOSITORY_ROOT / "deeplearning" / "training" / "requirements-adaface-export.txt"
)
EXPECTED_SHA256_ALLOWLIST = {
    "7baabf47c06391ab52e312134c6255846a5e55aa5a641f0553a89092fe2429d8",
    "7cb549232dd13071d9a12cd74cb9fa9741c9087021c1e6f1ae5ed994b5af7cfc",
}


def test_AdaFace_모델_배치는_수동_실행만_허용한다() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "\n  push:" not in workflow
    assert "\n  pull_request:" not in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "cancel-in-progress: false" in workflow


def test_AdaFace_모델은_고정_revision에서_생성하고_허용_hash를_확인한다() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    prepare_position = workflow.index("prepare_adaface_model")
    local_hash_position = workflow.index('model_sha256=$(sha256sum "$output"')
    transfer_position = workflow.index("rsync -lt")
    remote_hash_position = workflow.index(
        'actual=$(sha256sum "$temporary"', transfer_position
    )
    activation_position = workflow.index('mv -- "$temporary" "$destination"')

    assert all(value in workflow for value in EXPECTED_SHA256_ALLOWLIST)
    assert (
        prepare_position
        < local_hash_position
        < transfer_position
        < remote_hash_position
        < activation_position
    )


def test_AdaFace_모델_배치는_호스트_키를_검사하고_기존_비정상_파일을_보존한다() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "StrictHostKeyChecking=yes" in workflow
    assert "StrictHostKeyChecking=no" not in workflow
    assert "GPU_SERVER_KNOWN_HOSTS" in workflow
    assert 'backup="$destination.invalid-$RUN_ID"' in workflow
    assert 'mv -- "$destination" "$backup"' in workflow
    assert '[ ! -e "$backup" ] || mv -- "$backup" "$destination"' in workflow


def test_AdaFace_export_의존성은_CPU와_정확한_버전으로_고정한다() -> None:
    requirements = REQUIREMENTS_PATH.read_text(encoding="utf-8")
    package_lines = [
        line
        for line in requirements.splitlines()
        if line and not line.startswith(("#", "--"))
    ]

    assert "torch==2.7.1+cpu" in package_lines
    assert "torchvision==0.22.1+cpu" in package_lines
    assert "onnx==1.18.0" in package_lines
    assert "onnxruntime==1.22.0" in package_lines
    assert all("==" in line for line in package_lines)


def test_새_모델_배치_workflow도_GPU_배포_설정_검증을_실행한다() -> None:
    ci_workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    deployment_filter = ci_workflow.split("deployment:", 1)[1].split(
        "fastapi-checks:", 1
    )[0]
    assert "'.github/workflows/provision-adaface-model.yml'" in deployment_filter


def test_배포_사전점검도_같은_SHA_목록을_기대한다() -> None:
    """배치·변환·사전점검이 같은 검증 산출물 목록을 가리켜야 한다.

    셋 중 하나만 바뀌면 배치는 성공하는데 배포가 막히거나, 반대로 다른 가중치가
    통과한다. 사전점검의 기대 해시는 파일 이름이 아니라 내용을 보는 유일한 지점이다.
    """
    from validate_face_handover_deployment import FACE_MODEL_CONFIGS

    assert set(FACE_MODEL_CONFIGS["adaface"]["model_sha256_allowlist"]) == (
        EXPECTED_SHA256_ALLOWLIST
    )


def test_변환_스크립트도_같은_SHA_목록을_기대한다() -> None:
    source = (
        REPOSITORY_ROOT / "deeplearning" / "training" / "prepare_adaface_model.py"
    ).read_text(encoding="utf-8")

    assert all(value in source for value in EXPECTED_SHA256_ALLOWLIST)
