from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = (
    REPOSITORY_ROOT / ".github" / "workflows" / "provision-adaface-model.yml"
)
REQUIREMENTS_PATH = (
    REPOSITORY_ROOT
    / "deeplearning"
    / "training"
    / "requirements-adaface-export.txt"
)
EXPECTED_SHA256 = (
    "7baabf47c06391ab52e312134c6255846a5e55aa5a641f0553a89092fe2429d8"
)


def test_AdaFace_모델_배치는_수동_실행만_허용한다() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "\n  push:" not in workflow
    assert "\n  pull_request:" not in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "cancel-in-progress: false" in workflow


def test_AdaFace_모델은_고정_revision에서_생성하고_두_번_hash를_확인한다() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    prepare_position = workflow.index("prepare_adaface_model")
    local_hash_position = workflow.index("sha256sum --check --strict")
    transfer_position = workflow.index("rsync -lt")
    remote_hash_position = workflow.index(
        'actual=$(sha256sum "$temporary"', transfer_position
    )
    activation_position = workflow.index('mv -- "$temporary" "$destination"')

    assert EXPECTED_SHA256 in workflow
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
