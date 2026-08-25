from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import verify_face_handover_runtime as runtime_module
from verify_face_handover_runtime import (
    FASTAPI_CONTRACT_PROBE_CODE,
    FASTAPI_ENTRY_EVENT_PROBE_CODE,
    WORKER_METRICS_PROBE_CODE,
    RuntimeVerifier,
    metric_sum,
    parse_metric_samples,
)

METRICS = """
# HELP classroom_monitoring_frames_processed_total frames
# TYPE classroom_monitoring_frames_processed_total counter
classroom_monitoring_frames_processed_total{camera_id="entry-camera",result="ok"} 12
classroom_monitoring_frames_processed_total{camera_id="entry-camera",result="failed"} 1
classroom_monitoring_frames_processed_total{camera_id="classroom-cctv",result="ok"} 20
# HELP classroom_monitoring_face_identification_duration_seconds duration
classroom_monitoring_face_identification_requests_total{outcome="ok"} 3
classroom_monitoring_identity_handoff_total{outcome="accepted"} 1
"""


def test_prometheus_sample의_label과_값을_읽는다() -> None:
    samples = parse_metric_samples(
        METRICS, "classroom_monitoring_frames_processed_total"
    )

    assert samples == [
        ({"camera_id": "entry-camera", "result": "ok"}, 12.0),
        ({"camera_id": "entry-camera", "result": "failed"}, 1.0),
        ({"camera_id": "classroom-cctv", "result": "ok"}, 20.0),
    ]


def test_필요한_label의_metric만_합산한다() -> None:
    assert (
        metric_sum(
            METRICS,
            "classroom_monitoring_frames_processed_total",
            camera_id="entry-camera",
            result="ok",
        )
        == 12.0
    )


def test_없는_metric은_0이다() -> None:
    assert metric_sum(METRICS, "missing_metric") == 0.0


def test_worker_metrics_probe는_python_c에서_다시_파싱할_수_있다() -> None:
    """바깥 문자열 escape가 내부 `python -c` 문법을 깨뜨리지 않아야 한다."""
    compile(WORKER_METRICS_PROBE_CODE, "<worker-metrics-probe>", "exec")
    assert 'print("__FACE_CAMERAS__="' in WORKER_METRICS_PROBE_CODE
    assert "FASTAPI_URL" not in WORKER_METRICS_PROBE_CODE
    assert "/entry-identity-events" not in WORKER_METRICS_PROBE_CODE


def test_FastAPI_probe는_python_c에서_다시_파싱할_수_있다() -> None:
    compile(FASTAPI_CONTRACT_PROBE_CODE, "<fastapi-contract-probe>", "exec")
    compile(FASTAPI_ENTRY_EVENT_PROBE_CODE, "<entry-event-probe>", "exec")


def install_docker_fake(
    monkeypatch: pytest.MonkeyPatch, *, metrics: str = METRICS
) -> None:
    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if "config" in command and "--format" in command:
            output = (
                '{"services":{"deeplearning":{"image":"deep:test"},'
                '"inference-worker":{"image":"worker:test"}}}'
            )
        elif "ps" in command:
            output = "deep-id" if command[-1] == "deeplearning" else "worker-id"
        elif command[:2] == ["docker", "inspect"]:
            if "{{.State.Health.Status}}" in command:
                output = "healthy"
            else:
                output = "deep:test" if command[-1] == "deep-id" else "worker:test"
        elif "exec" in command and "/metrics" in command[-1]:
            output = metrics + "\n__FACE_CAMERAS__=entry-camera"
        elif "exec" in command and "/entry-identity-events" in command[-1]:
            output = "1"
        elif "exec" in command:
            output = "ok"
        else:  # pragma: no cover - 새 Docker 호출이 추가되면 테스트가 알려준다.
            raise AssertionError(command)
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    monkeypatch.setattr(runtime_module.subprocess, "run", run)


def test_실행_이미지_HTTP_CUDA_live_인계가_모두_있으면_통과한다(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_docker_fake(monkeypatch)
    verifier = RuntimeVerifier(tmp_path / "compose.yml")

    verifier.verify_containers()
    verifier.verify_network_and_gpu()
    verifier.verify_fastapi_contract(required=True)
    verifier.verify_metrics(require_live_handoff=True)

    assert verifier.errors == []


def test_live_인계가_아직_없으면_실패_이유를_남긴다(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_docker_fake(
        monkeypatch,
        metrics=METRICS.replace(
            'identity_handoff_total{outcome="accepted"} 1',
            'identity_handoff_total{outcome="accepted"} 0',
        ),
    )
    verifier = RuntimeVerifier(tmp_path / "compose.yml")

    verifier.verify_metrics(require_live_handoff=True)

    assert "성공한 CCTV 신원 인계가 아직 없습니다." in verifier.errors


def test_저장된_입구_이벤트가_아직_없으면_실패_이유를_남긴다(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_docker_fake(monkeypatch)
    original_run = runtime_module.subprocess.run

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        completed = original_run(command, **kwargs)
        if "exec" in command and "/entry-identity-events" in command[-1]:
            return subprocess.CompletedProcess(
                command,
                completed.returncode,
                stdout="0",
                stderr=completed.stderr,
            )
        return subprocess.CompletedProcess(
            command,
            completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    monkeypatch.setattr(runtime_module.subprocess, "run", run)
    verifier = RuntimeVerifier(tmp_path / "compose.yml")

    verifier.verify_metrics(require_live_handoff=True)

    assert (
        "FastAPI에서 저장된 입구 얼굴 이벤트를 조회하지 못했습니다." in verifier.errors
    )


def test_worker_metrics가_늦게_열리면_준비될_때까지_재시도한다(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    attempts = 0

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal attempts
        del kwargs
        attempts += 1
        if attempts < 3:
            raise subprocess.CalledProcessError(1, command)
        output = METRICS + "\n__FACE_CAMERAS__=entry-camera"
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    monkeypatch.setattr(runtime_module.subprocess, "run", run)
    monkeypatch.setattr(runtime_module.time, "sleep", lambda _seconds: None)
    verifier = RuntimeVerifier(
        tmp_path / "compose.yml",
        worker_readiness_timeout_seconds=30,
    )

    metrics, camera_ids = verifier.worker_metrics()

    assert attempts == 3
    assert metrics.strip() == METRICS.strip()
    assert camera_ids == {"entry-camera"}
    assert verifier.errors == []


def test_worker_metrics가_제한_시간_안에_열리지_않으면_실패한다(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(runtime_module.subprocess, "run", run)
    verifier = RuntimeVerifier(
        tmp_path / "compose.yml",
        worker_readiness_timeout_seconds=0,
    )

    metrics, camera_ids = verifier.worker_metrics()

    assert metrics == ""
    assert camera_ids == set()
    assert verifier.errors == [
        "worker /metrics가 0초 안에 준비되지 않았습니다. (1회 시도)"
    ]


def test_원격_FastAPI_계약_실패는_기본_GPU_배포를_막지_않는다(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        if "exec" in command and command[-1] == FASTAPI_CONTRACT_PROBE_CODE:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(runtime_module.subprocess, "run", run)
    verifier = RuntimeVerifier(tmp_path / "compose.yml")

    verifier.verify_fastapi_contract(required=False)

    assert verifier.errors == []
    assert verifier.warnings == [
        "원격 FastAPI HTTP 계약을 확인하지 못했습니다. GPU 서비스 배포는 계속합니다."
    ]


def test_live_인계_검증에서는_원격_FastAPI_계약이_필수다(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(runtime_module.subprocess, "run", run)
    verifier = RuntimeVerifier(tmp_path / "compose.yml")

    verifier.verify_fastapi_contract(required=True)

    assert verifier.errors == ["원격 FastAPI HTTP 계약을 확인하지 못했습니다."]
    assert verifier.warnings == []


def test_GPU_배포_workflow가_재기동_후_runtime을_검증한다() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    workflow = (
        repository_root / ".github" / "workflows" / "deploy-gpu-server.yml"
    ).read_text(encoding="utf-8")

    apply_position = workflow.index("up -d --force-recreate")
    verify_position = workflow.index(
        "python3 .docker/scripts/verify_face_handover_runtime.py"
    )

    assert verify_position > apply_position
    assert "--worker-readiness-timeout-seconds 120" in workflow


def test_GPU_배포_workflow가_현재_소스로_latest_두_개를_함께_갱신한다() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    workflow = (
        repository_root / ".github" / "workflows" / "deploy-gpu-server.yml"
    ).read_text(encoding="utf-8")

    assert "- 'worker/**'" in workflow
    assert "- 'deeplearning/**'" in workflow
    assert "git archive --format=tar HEAD:worker" in workflow
    assert "git archive --format=tar HEAD:deeplearning" in workflow
    assert "org.opencontainers.image.revision=$GITHUB_SHA" in workflow
    assert (
        "ghcr.io/where-is-jo/classroom-monitoring-deeplearning:candidate-$GITHUB_SHA"
    ) in workflow
    assert (
        "ghcr.io/where-is-jo/classroom-monitoring-worker:candidate-$GITHUB_SHA"
    ) in workflow

    deep_build = workflow.index("HEAD:deeplearning")
    worker_build = workflow.index("HEAD:worker")
    config_validation = workflow.index("서버에서 compose 검증 (실패 시 롤백)")
    deep_latest = workflow.index(
        'docker image tag "$DEEP_CANDIDATE" '
        "ghcr.io/where-is-jo/classroom-monitoring-deeplearning:latest"
    )
    worker_latest = workflow.index(
        'docker image tag "$WORKER_CANDIDATE" '
        "ghcr.io/where-is-jo/classroom-monitoring-worker:latest"
    )
    force_recreate = workflow.index("up -d --force-recreate")

    assert deep_build < config_validation < deep_latest < force_recreate
    assert worker_build < config_validation < worker_latest < force_recreate


def test_GPU_배포_workflow는_실패하면_이전_latest_image_ID를_복구한다() -> None:
    repository_root = Path(__file__).resolve().parents[3]
    workflow = (
        repository_root / ".github" / "workflows" / "deploy-gpu-server.yml"
    ).read_text(encoding="utf-8")

    backup_position = workflow.index("$backup_dir/$STAMP.images")
    activation_position = workflow.index("candidate를 latest 이미지로 전환")
    rollback_position = workflow.index("rollback()")
    restore_deep = workflow.index(
        'docker image tag "$deep_id" '
        "ghcr.io/where-is-jo/classroom-monitoring-deeplearning:latest",
        rollback_position,
    )
    restore_worker = workflow.index(
        'docker image tag "$worker_id" '
        "ghcr.io/where-is-jo/classroom-monitoring-worker:latest",
        rollback_position,
    )

    assert backup_position < activation_position < rollback_position
    assert restore_deep > rollback_position
    assert restore_worker > rollback_position
