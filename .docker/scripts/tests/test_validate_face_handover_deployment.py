from __future__ import annotations

import hashlib
import json
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
        "FACE_GALLERY_DATABASE_NAME=classroom\n"
        "FACE_RECOGNIZER=arcface\n"
        "FACE_RECOGNITION_MODEL_PATH=/models/face/buffalo_l/w600k_r50.onnx\n"
        "FACE_RECOGNITION_MODEL_VERSION=insightface-buffalo_l-w600k_r50-v0.7\n"
        "FACE_IDENTIFICATION_ENABLED=true\n"
        "FACE_IDENTITY_THRESHOLD_FILE=/models/face/config/thresholds.json\n"
        "FACE_EMBEDDING_COLLECTION=face_embeddings_arcface\n",
        encoding="utf-8",
    )
    (env_dir / "fastapi.dev.env").write_text(
        "FACE_RECOGNIZER=arcface\n",
        encoding="utf-8",
    )
    (env_dir / "worker.dev.env").write_text(
        "STREAM_SOURCES=entry-camera=rtsp://mediamtx:8554/entry-camera,"
        "classroom-cctv=rtsp://mediamtx:8554/classroom-cctv\n"
        "MODEL_PATH=/models/person.pt\n"
        "MODEL_CONTRACT_PATH=/models/person.contract.json\n"
        "INFERENCE_DEVICE=cuda\n"
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
    model_hash = hashlib.sha256((models / "person.pt").read_bytes()).hexdigest()
    (models / "person.contract.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model_sha256": model_hash,
                "target_class_ids": {"0": "person"},
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
    return docker_root


def test_필수_환경과_모델이_모두_있으면_통과한다(tmp_path: Path) -> None:
    docker_root = build_deployment(tmp_path)

    assert validate(docker_root) == []


def test_target_far가_0점001_이하면_통과한다(tmp_path: Path) -> None:
    docker_root = build_deployment(tmp_path)
    threshold_path = docker_root / "models" / "face" / "config" / "thresholds.json"
    thresholds = json.loads(threshold_path.read_text(encoding="utf-8"))
    thresholds["target_far"] = 0.0005
    threshold_path.write_text(json.dumps(thresholds), encoding="utf-8")

    assert validate(docker_root) == []


def test_target_far가_0점001을_초과하면_실패한다(tmp_path: Path) -> None:
    docker_root = build_deployment(tmp_path)
    threshold_path = docker_root / "models" / "face" / "config" / "thresholds.json"
    thresholds = json.loads(threshold_path.read_text(encoding="utf-8"))
    thresholds["target_far"] = 0.0011
    threshold_path.write_text(json.dumps(thresholds), encoding="utf-8")

    errors = validate(docker_root)

    assert any("target_far" in error for error in errors)


def test_사람_탐지_모델_해시가_계약과_다르면_실패한다(tmp_path: Path) -> None:
    docker_root = build_deployment(tmp_path)
    (docker_root / "models" / "person.pt").write_bytes(b"changed-model")

    errors = validate(docker_root)

    assert any("SHA-256" in error for error in errors)


def test_필수_전처리가_있는_모델이면_실패한다(tmp_path: Path) -> None:
    docker_root = build_deployment(tmp_path)
    contract_path = docker_root / "models" / "person.contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["preprocessing_contract"] = {
        "schema_version": 1,
        "method": "uniform-full-frame-pixelation-v1",
        "label_derived": False,
        "training_compatible": True,
        "inference_preprocessing_required": True,
        "pixelation_block_size": 8,
    }
    contract_path.write_text(json.dumps(contract), encoding="utf-8")

    errors = validate(docker_root)

    assert any("원본 프레임 모델" in error for error in errors)


def test_thresholds_json이_없으면_실패한다(tmp_path: Path) -> None:
    docker_root = build_deployment(tmp_path)
    (docker_root / "models" / "face" / "config" / "thresholds.json").unlink()

    errors = validate(docker_root)

    assert any("임계값 파일이 없습니다" in error for error in errors)


def test_track_임계값이_누락되면_실패한다(tmp_path: Path) -> None:
    docker_root = build_deployment(tmp_path)
    threshold_path = docker_root / "models" / "face" / "config" / "thresholds.json"
    thresholds = json.loads(threshold_path.read_text(encoding="utf-8"))
    del thresholds["track_similarity_threshold"]
    threshold_path.write_text(json.dumps(thresholds), encoding="utf-8")

    errors = validate(docker_root)

    assert any("track_similarity_threshold" in error for error in errors)


def test_AdaFace_모델_컬렉션_임계값을_같이_전환하면_통과한다(
    tmp_path: Path,
) -> None:
    docker_root = build_deployment(tmp_path)
    model_path = docker_root / "models" / "face" / "adaface"
    model_path.mkdir()
    (model_path / "adaface_ir50_webface4m.onnx").write_bytes(b"adaface-model")
    deep_env_path = docker_root / "env" / "deeplearning.dev.env"
    deep_env = deep_env_path.read_text(encoding="utf-8")
    deep_env = deep_env.replace("FACE_RECOGNIZER=arcface", "FACE_RECOGNIZER=adaface")
    deep_env = deep_env.replace(
        "/models/face/buffalo_l/w600k_r50.onnx",
        "/models/face/adaface/adaface_ir50_webface4m.onnx",
    )
    deep_env = deep_env.replace(
        "insightface-buffalo_l-w600k_r50-v0.7",
        "cvlface-adaface-ir50-webface4m-fe7718c6",
    )
    deep_env = deep_env.replace("face_embeddings_arcface", "face_embeddings_adaface")
    deep_env_path.write_text(deep_env, encoding="utf-8")
    threshold_path = docker_root / "models" / "face" / "config" / "thresholds.json"
    thresholds = json.loads(threshold_path.read_text(encoding="utf-8"))
    thresholds.update(
        {
            "model_name": "adaface",
            "model_version": "cvlface-adaface-ir50-webface4m-fe7718c6",
            "preprocessing_version": "cvlface-rgb-norm-crop-112-v1",
            "similarity_threshold": 0.37,
        }
    )
    threshold_path.write_text(json.dumps(thresholds), encoding="utf-8")
    (docker_root / "env" / "fastapi.dev.env").write_text(
        "FACE_RECOGNIZER=adaface\nSTUDENT_IDENTITY_CONFIDENCE_THRESHOLD_ADAFACE=0.37\n",
        encoding="utf-8",
    )

    assert validate(docker_root) == []


def test_AdaFace가_ArcFace_컬렉션을_가리키면_실패한다(tmp_path: Path) -> None:
    docker_root = build_deployment(tmp_path)
    deep_env_path = docker_root / "env" / "deeplearning.dev.env"
    deep_env_path.write_text(
        deep_env_path.read_text(encoding="utf-8").replace(
            "FACE_RECOGNIZER=arcface", "FACE_RECOGNIZER=adaface"
        ),
        encoding="utf-8",
    )

    errors = validate(docker_root)

    assert any("FACE_EMBEDDING_COLLECTION" in error for error in errors)


def test_FastAPI와_AI서버의_활성_모델이_다르면_실패한다(tmp_path: Path) -> None:
    docker_root = build_deployment(tmp_path)
    (docker_root / "env" / "fastapi.dev.env").write_text(
        "FACE_RECOGNIZER=adaface\nSTUDENT_IDENTITY_CONFIDENCE_THRESHOLD_ADAFACE=0.37\n",
        encoding="utf-8",
    )

    errors = validate(docker_root)

    assert any("FACE_RECOGNIZER가 다릅니다" in error for error in errors)


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
