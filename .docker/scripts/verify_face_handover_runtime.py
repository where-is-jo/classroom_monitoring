#!/usr/bin/env python3
"""기동된 GPU Compose의 얼굴 식별 → CCTV 인계 런타임을 검증한다."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

RUNTIME_SERVICES = ("deeplearning", "inference-worker")
METRIC_PREFIX = "classroom_monitoring_"
DEFAULT_WORKER_READINESS_TIMEOUT_SECONDS = 120.0
WORKER_READINESS_RETRY_INTERVAL_SECONDS = 2.0
_LABEL_PATTERN = re.compile(r'(\w+)="((?:\\.|[^"\\])*)"')


def parse_metric_samples(
    text: str, metric_name: str
) -> list[tuple[dict[str, str], float]]:
    """Prometheus text에서 지정 metric sample만 읽는다."""
    samples: list[tuple[dict[str, str], float]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            identifier, raw_value = line.rsplit(None, 1)
        except ValueError:
            continue
        sample_name = identifier.split("{", 1)[0]
        if sample_name != metric_name:
            continue
        labels = {
            key: value.replace(r"\"", '"').replace(r"\\", "\\")
            for key, value in _LABEL_PATTERN.findall(identifier)
        }
        try:
            samples.append((labels, float(raw_value)))
        except ValueError:
            continue
    return samples


def metric_sum(text: str, metric_name: str, **required_labels: str) -> float:
    return sum(
        value
        for labels, value in parse_metric_samples(text, metric_name)
        if all(labels.get(key) == expected for key, expected in required_labels.items())
    )


class RuntimeVerifier:
    def __init__(
        self,
        compose_file: Path,
        *,
        worker_readiness_timeout_seconds: float = (
            DEFAULT_WORKER_READINESS_TIMEOUT_SECONDS
        ),
        worker_readiness_retry_interval_seconds: float = (
            WORKER_READINESS_RETRY_INTERVAL_SECONDS
        ),
    ) -> None:
        self._compose = ["docker", "compose", "-f", str(compose_file.resolve())]
        self._worker_readiness_timeout_seconds = max(
            0.0, worker_readiness_timeout_seconds
        )
        self._worker_readiness_retry_interval_seconds = max(
            0.1, worker_readiness_retry_interval_seconds
        )
        self.errors: list[str] = []

    def _run(self, command: list[str]) -> str:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return completed.stdout.strip()

    def _container_id(self, service: str) -> str:
        try:
            container_id = self._run([*self._compose, "ps", "-q", service])
        except (OSError, subprocess.CalledProcessError):
            self.errors.append(f"{service} 컨테이너 상태를 읽지 못했습니다.")
            return ""
        if not container_id:
            self.errors.append(f"{service} 컨테이너가 실행 중이 아닙니다.")
        return container_id

    def verify_containers(self) -> None:
        try:
            configured = json.loads(
                self._run([*self._compose, "config", "--format", "json"])
            )
            expected_images = {
                service: configured["services"][service]["image"]
                for service in RUNTIME_SERVICES
            }
        except (
            OSError,
            KeyError,
            TypeError,
            json.JSONDecodeError,
            subprocess.CalledProcessError,
        ):
            self.errors.append("Compose의 런타임 이미지 설정을 읽지 못했습니다.")
            expected_images = {}

        for service in RUNTIME_SERVICES:
            container_id = self._container_id(service)
            if not container_id:
                continue
            try:
                actual_image = self._run(
                    ["docker", "inspect", "--format", "{{.Config.Image}}", container_id]
                )
            except (OSError, subprocess.CalledProcessError):
                self.errors.append(f"{service} 이미지 정보를 읽지 못했습니다.")
                continue
            expected_image = expected_images.get(service)
            if expected_image is not None and actual_image != expected_image:
                self.errors.append(
                    f"{service}가 Compose의 고정 이미지 태그로 실행되지 않았습니다."
                )

        deep_id = self._container_id("deeplearning")
        if deep_id:
            try:
                health = self._run(
                    [
                        "docker",
                        "inspect",
                        "--format",
                        "{{.State.Health.Status}}",
                        deep_id,
                    ]
                )
            except (OSError, subprocess.CalledProcessError):
                self.errors.append("deeplearning health 상태를 읽지 못했습니다.")
            else:
                if health != "healthy":
                    self.errors.append("deeplearning이 healthy 상태가 아닙니다.")

    def verify_network_and_gpu(self) -> None:
        python_code = """
import json
import os
import urllib.request

with urllib.request.urlopen("http://deeplearning:8100/health/ready", timeout=8) as response:
    ready = json.load(response)
assert ready == {"status": "ready", "face_identification": "ready"}
with urllib.request.urlopen("http://deeplearning:8100/openapi.json", timeout=8) as response:
    openapi = json.load(response)
assert "/internal/face-identifications" in openapi["paths"]
base_url = os.environ["FASTAPI_URL"].rstrip("/")
with urllib.request.urlopen(base_url + "/health/ready", timeout=8) as response:
    assert response.status == 200
print("http-contracts-ok")
"""
        gpu_code = """
import torch
assert torch.cuda.is_available()
assert torch.cuda.device_count() >= 1
print("cuda-ok")
"""
        for description, code in (
            ("서비스 간 HTTP 계약", python_code),
            ("worker CUDA", gpu_code),
        ):
            try:
                self._run(
                    [
                        *self._compose,
                        "exec",
                        "-T",
                        "inference-worker",
                        "python",
                        "-c",
                        code,
                    ]
                )
            except (OSError, subprocess.CalledProcessError):
                self.errors.append(f"{description} 검증에 실패했습니다.")

    def worker_metrics(self) -> tuple[str, set[str]]:
        """모델 초기화 뒤 열리는 worker metrics를 제한 시간 동안 기다린다."""
        code = """
import os
import urllib.request

with urllib.request.urlopen("http://127.0.0.1:9101/metrics", timeout=5) as response:
    print(response.read().decode("utf-8"), end="")
print("\n__FACE_CAMERAS__=" + os.environ.get("FACE_IDENTITY_CAMERA_IDS", ""))
"""
        command = [
            *self._compose,
            "exec",
            "-T",
            "inference-worker",
            "python",
            "-c",
            code,
        ]
        deadline = time.monotonic() + self._worker_readiness_timeout_seconds
        attempts = 0
        while True:
            attempts += 1
            try:
                output = self._run(command)
                break
            except OSError:
                self.errors.append("worker /metrics 검증 명령을 실행하지 못했습니다.")
                return "", set()
            except subprocess.CalledProcessError:
                now = time.monotonic()
                if now >= deadline:
                    timeout = f"{self._worker_readiness_timeout_seconds:g}"
                    self.errors.append(
                        f"worker /metrics가 {timeout}초 안에 준비되지 않았습니다. "
                        f"({attempts}회 시도)"
                    )
                    return "", set()
                time.sleep(
                    min(
                        self._worker_readiness_retry_interval_seconds,
                        deadline - now,
                    )
                )
        marker = "\n__FACE_CAMERAS__="
        if marker not in output:
            self.errors.append("worker 얼굴 카메라 설정을 확인하지 못했습니다.")
            return output, set()
        metrics, raw_camera_ids = output.rsplit(marker, 1)
        camera_ids = {
            item.strip() for item in raw_camera_ids.split(",") if item.strip()
        }
        return metrics, camera_ids

    def verify_metrics(self, *, require_live_handoff: bool) -> None:
        metrics, face_camera_ids = self.worker_metrics()
        if not metrics:
            return
        required_families = (
            "face_identification_requests_total",
            "face_identification_duration_seconds",
            "identity_handoff_total",
        )
        for family in required_families:
            if f"{METRIC_PREFIX}{family}" not in metrics:
                self.errors.append(f"worker 지표 {family}가 없습니다.")
        if not require_live_handoff:
            return
        required_cameras = face_camera_ids | {"classroom-cctv"}
        if not face_camera_ids:
            self.errors.append("FACE_IDENTITY_CAMERA_IDS가 비어 있습니다.")
        for camera_id in required_cameras:
            processed = metric_sum(
                metrics,
                f"{METRIC_PREFIX}frames_processed_total",
                camera_id=camera_id,
                result="ok",
            )
            if processed <= 0:
                self.errors.append(f"{camera_id}의 정상 처리 프레임이 아직 없습니다.")
        if (
            metric_sum(
                metrics,
                f"{METRIC_PREFIX}face_identification_requests_total",
                outcome="ok",
            )
            <= 0
        ):
            self.errors.append("성공한 얼굴 식별 호출이 아직 없습니다.")
        if (
            metric_sum(
                metrics,
                f"{METRIC_PREFIX}identity_handoff_total",
                outcome="accepted",
            )
            <= 0
        ):
            self.errors.append("성공한 CCTV 신원 인계가 아직 없습니다.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--compose-file",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "compose.main.dev.gpu.yml",
    )
    parser.add_argument(
        "--require-live-handoff",
        action="store_true",
        help="두 카메라 프레임·얼굴 호출·accepted 인계가 실제로 발생했는지 확인",
    )
    parser.add_argument(
        "--worker-readiness-timeout-seconds",
        type=float,
        default=DEFAULT_WORKER_READINESS_TIMEOUT_SECONDS,
        help="모델 초기화 뒤 worker /metrics가 열릴 때까지 기다릴 최대 시간",
    )
    args = parser.parse_args()
    verifier = RuntimeVerifier(
        args.compose_file,
        worker_readiness_timeout_seconds=args.worker_readiness_timeout_seconds,
    )
    verifier.verify_containers()
    verifier.verify_network_and_gpu()
    verifier.verify_metrics(require_live_handoff=args.require_live_handoff)
    if verifier.errors:
        print("얼굴 식별 → 객체 추적 인계 런타임 검증 실패:", file=sys.stderr)
        for error in verifier.errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("얼굴 식별 → 객체 추적 인계 런타임 검증을 통과했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
