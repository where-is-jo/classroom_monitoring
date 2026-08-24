from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import verify_face_handover_runtime as runtime_module
from verify_face_handover_runtime import (
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
