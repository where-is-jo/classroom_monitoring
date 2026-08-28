from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from validate_face_handover_deployment import (
    EXPECTED_THRESHOLD_METADATA,
    validate,
)


def build_deployment(tmp_path: Path) -> Path:
    docker_root = tmp_path / ".docker"
    env_dir = docker_root / "env"
    models = docker_root / "models"
    env_dir.mkdir(parents=True)
    (models / "face" / "scrfd").mkdir(parents=True)
    (models / "face" / "mediapipe").mkdir(parents=True)
    (models / "face" / "buffalo_l").mkdir(parents=True)
    (models / "face" / "config").mkdir(parents=True)
    (env_dir / "deeplearning.dev.env").write_text(
        "FACE_GALLERY_DATABASE_URL=mongodb://example.invalid\n"
        "FACE_GALLERY_DATABASE_NAME=classroom\n",
        encoding="utf-8",
    )
    (env_dir / "worker.dev.env").write_text(
        "STREAM_SOURCES=entry-camera=rtsp://mediamtx:8554/entry-camera,"
        "classroom-cctv=rtsp://mediamtx:8554/classroom-cctv\n"
        "MODEL_PATH=/models/person.pt\n"
        "MODEL_CONTRACT_PATH=/models/person.model_contract.json\n"
        "INFERENCE_DEVICE=cuda\n"
        "INFERENCE_IMAGE_SIZE=1280\n"
        'INFERENCE_TARGET_CLASS_IDS={"0":"person"}\n'
        "FASTAPI_URL=http://fastapi.example.invalid:8076\n"
        "FACE_IDENTITY_URL=http://deeplearning:8100\n"
        "FACE_IDENTITY_CAMERA_IDS=entry-camera\n"
        "PERSON_TRACKING_CAMERA_IDS=classroom-cctv\n",
        encoding="utf-8",
    )
    for relative in (
        "face/scrfd/scrfd_10g_bnkps.onnx",
        "face/mediapipe/face_landmarker.task",
        "face/buffalo_l/w600k_r50.onnx",
        "person.pt",
    ):
        (models / relative).write_bytes(b"model")
    (models / "person.model_contract.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model_sha256": hashlib.sha256(b"model").hexdigest(),
                "target_class_ids": {"0": "person"},
                "image_size": 1280,
                "preprocessing_contract": {
                    "schema_version": 1,
                    "method": "original-frame-v1",
                    "label_derived": False,
                    "training_compatible": True,
                    "inference_preprocessing_required": False,
                },
            }
        ),
        encoding="utf-8",
    )
    thresholds = {
        **EXPECTED_THRESHOLD_METADATA,
        "similarity_threshold": 0.5,
        "margin_threshold": 0.1,
        "track_similarity_threshold": 0.7,
        "target_far": 0.001,
        "track_target_false_association": 0.001,
    }
    (models / "face" / "config" / "thresholds.json").write_text(
        json.dumps(thresholds), encoding="utf-8"
    )
    return docker_root


def test_필수_환경과_모델이_모두_있으면_통과한다(tmp_path: Path) -> None:
    docker_root = build_deployment(tmp_path)

    assert validate(docker_root) == []


def test_검증기는_worker_소스가_없는_배포_경계에서도_실행된다(
    tmp_path: Path,
) -> None:
    docker_root = build_deployment(tmp_path)
    source_scripts = Path(__file__).resolve().parents[1]
    target_scripts = docker_root / "scripts"
    target_scripts.mkdir()
    for name in (
        "validate_face_handover_deployment.py",
        "deployment_person_model_contract.py",
    ):
        shutil.copy2(source_scripts / name, target_scripts / name)

    completed = subprocess.run(
        [
            sys.executable,
            str(target_scripts / "validate_face_handover_deployment.py"),
            "--docker-root",
            str(docker_root),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_thresholds_json이_없으면_실패한다(tmp_path: Path) -> None:
    docker_root = build_deployment(tmp_path)
    (docker_root / "models" / "face" / "config" / "thresholds.json").unlink()

    errors = validate(docker_root)

    assert any("임계값 파일이 없습니다" in error for error in errors)


def test_사람_모델_계약이_없으면_실패한다(tmp_path: Path) -> None:
    docker_root = build_deployment(tmp_path)
    (docker_root / "models" / "person.model_contract.json").unlink()

    errors = validate(docker_root)

    assert any("person.model_contract.json" in error for error in errors)


def test_사람_모델_해시가_계약과_다르면_실패한다(tmp_path: Path) -> None:
    docker_root = build_deployment(tmp_path)
    (docker_root / "models" / "person.pt").write_bytes(b"different")

    errors = validate(docker_root)

    assert any("SHA-256" in error for error in errors)


def test_사람_모델_image_size가_계약과_다르면_실패한다(tmp_path: Path) -> None:
    docker_root = build_deployment(tmp_path)
    worker_env = docker_root / "env" / "worker.dev.env"
    worker_env.write_text(
        worker_env.read_text(encoding="utf-8").replace(
            "INFERENCE_IMAGE_SIZE=1280", "INFERENCE_IMAGE_SIZE=640"
        ),
        encoding="utf-8",
    )

    errors = validate(docker_root)

    assert any("INFERENCE_IMAGE_SIZE" in error for error in errors)


def test_track_임계값이_누락되면_실패한다(tmp_path: Path) -> None:
    docker_root = build_deployment(tmp_path)
    threshold_path = docker_root / "models" / "face" / "config" / "thresholds.json"
    thresholds = json.loads(threshold_path.read_text(encoding="utf-8"))
    del thresholds["track_similarity_threshold"]
    threshold_path.write_text(json.dumps(thresholds), encoding="utf-8")

    errors = validate(docker_root)

    assert any("track_similarity_threshold" in error for error in errors)


def test_입구_카메라가_스트림에_없으면_실패한다(tmp_path: Path) -> None:
    docker_root = build_deployment(tmp_path)
    worker_env = docker_root / "env" / "worker.dev.env"
    worker_env.write_text(
        worker_env.read_text(encoding="utf-8").replace(
            "FACE_IDENTITY_CAMERA_IDS=entry-camera",
            "FACE_IDENTITY_CAMERA_IDS=missing-entry",
        ),
        encoding="utf-8",
    )

    errors = validate(docker_root)

    assert any("STREAM_SOURCES에 모두 포함" in error for error in errors)


def test_잘못된_정적_인계_route는_실패한다(tmp_path: Path) -> None:
    docker_root = build_deployment(tmp_path)
    worker_env = docker_root / "env" / "worker.dev.env"
    worker_env.write_text(
        worker_env.read_text(encoding="utf-8") + "IDENTITY_HANDOVER_ROUTES=[]\n",
        encoding="utf-8",
    )

    errors = validate(docker_root)

    assert any("IDENTITY_HANDOVER_ROUTES" in error for error in errors)


def test_입구_카메라에_사람_탐지_역할도_주면_실패한다(tmp_path: Path) -> None:
    docker_root = build_deployment(tmp_path)
    worker_env = docker_root / "env" / "worker.dev.env"
    worker_env.write_text(
        worker_env.read_text(encoding="utf-8").replace(
            "PERSON_TRACKING_CAMERA_IDS=classroom-cctv",
            "PERSON_TRACKING_CAMERA_IDS=entry-camera,classroom-cctv",
        ),
        encoding="utf-8",
    )

    errors = validate(docker_root)

    assert any("역할은 겹칠 수 없습니다" in error for error in errors)


@pytest.mark.parametrize("threshold", ["0.5", "0.8"])
def test_YOLO_임계값이_ByteTrack_high_이상이면_실패한다(
    tmp_path: Path, threshold: str
) -> None:
    docker_root = build_deployment(tmp_path)
    worker_env = docker_root / "env" / "worker.dev.env"
    worker_env.write_text(
        worker_env.read_text(encoding="utf-8")
        + f"INFERENCE_CONFIDENCE_THRESHOLD={threshold}\n",
        encoding="utf-8",
    )

    errors = validate(docker_root)

    assert any("ByteTrack high" in error for error in errors)


def test_GPU_compose는_재빌드할_때_덮어쓰는_latest_이미지명을_쓴다() -> None:
    compose_path = Path(__file__).resolve().parents[2] / "compose.main.dev.gpu.yml"
    compose = compose_path.read_text(encoding="utf-8")

    for image in (
        "ghcr.io/where-is-jo/classroom-monitoring-deeplearning:latest",
        "ghcr.io/where-is-jo/classroom-monitoring-worker:latest",
    ):
        assert f"    image: {image}\n    pull_policy: never\n" in compose
    assert "local-202" not in compose
    assert "ghcr.io/where-is-jo/classroom-monitoring-deeplearning:local" not in compose
    assert "ghcr.io/where-is-jo/classroom-monitoring-worker:local" not in compose
