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
    FACE_MODEL_CONFIGS,
    validate,
)

# 테스트는 실제 174MB ONNX를 둘 수 없다. 더미 바이트를 쓰되 기대 해시를 그 더미의
# 것으로 바꿔, "해시를 대조한다"는 동작 자체는 그대로 검증한다.
_DUMMY_ADAFACE = b"adaface-model"


@pytest.fixture
def adaface_hash_matches_dummy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        FACE_MODEL_CONFIGS["adaface"],
        "model_sha256_allowlist",
        (hashlib.sha256(_DUMMY_ADAFACE).hexdigest(),),
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


def test_필수_전처리가_있는_모델이면_실패한다(tmp_path: Path) -> None:
    docker_root = build_deployment(tmp_path)
    contract_path = docker_root / "models" / "person.model_contract.json"
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


def test_AdaFace_모델_컬렉션_임계값을_같이_전환하면_통과한다(
    tmp_path: Path,
    adaface_hash_matches_dummy: None,
) -> None:
    docker_root = build_deployment(tmp_path)
    model_path = docker_root / "models" / "face" / "adaface"
    model_path.mkdir()
    (model_path / "adaface_ir50_webface4m.onnx").write_bytes(_DUMMY_ADAFACE)
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


def test_GPU_develop은_사람_탐지_후처리를_명시적으로_켠다() -> None:
    compose_path = Path(__file__).resolve().parents[2] / "compose.main.dev.gpu.yml"
    compose = compose_path.read_text(encoding="utf-8")

    worker = compose.split("  inference-worker:", 1)[1].split("\n  mediamtx:", 1)[0]
    assert 'PERSON_DETECTION_POSTPROCESS_ENABLED: "true"' in worker
    assert 'BYTETRACK_KALMAN_ENABLED: "true"' in worker


def test_gpu_server_passes_without_fastapi_env(tmp_path: Path) -> None:
    """`fastapi.dev.env`가 없는 것이 GPU 서버의 정상 상태다.

    README.server.md가 그 파일을 여기 두지 말라고 명시한다 — fastapi는 개인 PC로
    갔고 여기서는 읽히지 않는데 MongoDB Atlas 접속 정보만 공용 장비에 남기 때문이다.
    필수로 요구하면 문서를 따르는 배포가 전부 실패하고 롤백된다.
    """
    docker_root = build_deployment(tmp_path)
    (docker_root / "env" / "fastapi.dev.env").unlink()

    assert validate(docker_root) == []


def test_이름만_맞춘_다른_가중치는_실패한다(
    tmp_path: Path,
    adaface_hash_matches_dummy: None,
) -> None:
    """경로 이름이 맞아도 내용이 다르면 막는다.

    이름만 맞추면 경로 대조는 통과한다. 런타임에도 해시 검사가 없어
    (deeplearning/face_recognizer.py는 존재와 크기만 본다) 어떤 가중치든 선언된
    model_version으로 라벨링돼 갤러리에 들어간다. 실제로 운영 서버가 이 상태였다.
    """
    docker_root = build_deployment(tmp_path)
    model_path = docker_root / "models" / "face" / "adaface"
    model_path.mkdir()
    # 이름은 정본과 같지만 바이트열이 다르다.
    (model_path / "adaface_ir50_webface4m.onnx").write_bytes(b"another-adaface")
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

    errors = validate(docker_root)

    assert any("가중치가" in error for error in errors), errors


def test_adaface_rejects_arcface_weights(tmp_path: Path) -> None:
    """모델 경로가 선택한 인식기의 것인지 본다.

    두 모델 모두 (N,3,112,112) -> (N,512)라 형태로는 구분되지 않는다. 경로를 대조하지
    않으면 ArcFace 벡터가 AdaFace로 라벨링돼 갤러리에 들어간다.
    """
    docker_root = build_deployment(tmp_path)
    env_path = docker_root / "env" / "deeplearning.dev.env"
    env_path.write_text(
        env_path.read_text(encoding="utf-8")
        .replace("FACE_RECOGNIZER=arcface", "FACE_RECOGNIZER=adaface")
        .replace(
            "FACE_RECOGNITION_MODEL_VERSION=insightface-buffalo_l-w600k_r50-v0.7",
            "FACE_RECOGNITION_MODEL_VERSION=cvlface-adaface-ir50-webface4m-fe7718c6",
        )
        .replace(
            "FACE_EMBEDDING_COLLECTION=face_embeddings_arcface",
            "FACE_EMBEDDING_COLLECTION=face_embeddings_adaface",
        ),
        encoding="utf-8",
    )
    # 경로만 ArcFace 그대로 남겨 둔다 — 운영자가 잊기 가장 쉬운 값이다.
    errors = validate(docker_root)

    assert any("FACE_RECOGNITION_MODEL_PATH" in error for error in errors), errors
