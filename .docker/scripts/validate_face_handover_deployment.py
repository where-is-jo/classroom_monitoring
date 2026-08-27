#!/usr/bin/env python3
"""GPU 호스트의 얼굴 식별 → CCTV 신원 인계 배포 입력을 비밀 출력 없이 검증한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

EXPECTED_THRESHOLD_METADATA = {
    "model_name": "arcface",
    "model_version": "insightface-buffalo_l-w600k_r50-v0.7",
    "preprocessing_version": "insightface-norm-crop-112-v1",
}
# **네 번째 값(model_path)을 빠뜨리면 교차 연결이 열린다.** deeplearning의
# `_MODEL_DEFAULTS`는 인식기당 (버전, 전처리, 컬렉션, 상대 경로) 4-튜플인데 여기에는
# 앞의 셋만 옮겨져 있었다. 그래서 AdaFace 설정에 ArcFace 가중치를 물려도 대조할
# 근거가 없어 그대로 통과했다 — 두 모델 모두 (N,3,112,112) -> (N,512)라 형태로는
# 구분되지 않고, ArcFace 벡터가 AdaFace로 라벨링돼 갤러리에 들어간다.
FACE_MODEL_CONFIGS = {
    "arcface": {
        "metadata": EXPECTED_THRESHOLD_METADATA,
        "collection": "face_embeddings_arcface",
        "model_path": "buffalo_l/w600k_r50.onnx",
    },
    "adaface": {
        "metadata": {
            "model_name": "adaface",
            "model_version": "cvlface-adaface-ir50-webface4m-fe7718c6",
            "preprocessing_version": "cvlface-rgb-norm-crop-112-v1",
        },
        "collection": "face_embeddings_adaface",
        "model_path": "adaface/adaface_ir50_webface4m.onnx",
    },
}
COMMON_FACE_MODEL_PATHS = (
    "/models/face/scrfd/scrfd_10g_bnkps.onnx",
    "/models/face/mediapipe/face_landmarker.task",
)
ORIGINAL_FRAME_METHOD = "original-frame-v1"


def read_env(path: Path) -> dict[str, str]:
    """간단한 Docker env-file을 읽는다. 값은 호출자가 출력하지 않는다."""
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        if "=" not in line:
            raise ValueError(f"{path.name}:{line_number}: KEY=VALUE 형식이 아닙니다.")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not key:
            raise ValueError(f"{path.name}:{line_number}: 변수 이름이 비어 있습니다.")
        values[key] = value
    return values


def host_model_path(docker_root: Path, container_path: str) -> Path:
    prefix = "/models/"
    if not container_path.startswith(prefix):
        raise ValueError(f"모델 경로는 {prefix} 아래여야 합니다.")
    relative = Path(*container_path.removeprefix(prefix).split("/"))
    return docker_root / "models" / relative


def parse_camera_ids(stream_sources: str) -> set[str]:
    camera_ids: set[str] = set()
    for item in stream_sources.split(","):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            raise ValueError("STREAM_SOURCES는 camera_id=rtsp_url 형식이어야 합니다.")
        camera_id, url = (part.strip() for part in item.split("=", 1))
        if not camera_id or camera_id in camera_ids:
            raise ValueError("STREAM_SOURCES의 camera_id가 비었거나 중복됐습니다.")
        parsed = urlparse(url)
        if parsed.scheme not in {"rtsp", "rtsps"} or not parsed.netloc:
            raise ValueError("STREAM_SOURCES에 올바르지 않은 RTSP URL이 있습니다.")
        camera_ids.add(camera_id)
    if not camera_ids:
        raise ValueError("STREAM_SOURCES에 카메라가 없습니다.")
    return camera_ids


def validate_thresholds(
    path: Path,
    expected_metadata: dict[str, str] | None = None,
) -> list[str]:
    expected_metadata = expected_metadata or EXPECTED_THRESHOLD_METADATA
    errors: list[str] = []
    if not path.is_file():
        return [f"임계값 파일이 없습니다: {path}"]
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [f"임계값 파일을 JSON으로 읽을 수 없습니다: {path}"]
    if not isinstance(values, dict):
        return [f"임계값 JSON 최상위 값은 object여야 합니다: {path}"]
    for key, expected in expected_metadata.items():
        if values.get(key) != expected:
            errors.append(f"thresholds.json의 {key}가 현재 얼굴 모델과 다릅니다.")
    for key, upper in (
        ("similarity_threshold", 1.0),
        ("margin_threshold", 2.0),
        ("track_similarity_threshold", 1.0),
    ):
        value = values.get(key)
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or not 0.0 <= float(value) <= upper
        ):
            errors.append(f"thresholds.json의 {key} 범위가 올바르지 않습니다.")
    target_far = values.get("target_far")
    if (
        not isinstance(target_far, (int, float))
        or isinstance(target_far, bool)
        or not math.isfinite(float(target_far))
        or not 0.0 <= float(target_far) <= 0.001
    ):
        errors.append("thresholds.json의 target_far는 0.001 이하여야 합니다.")
    track_target = values.get("track_target_false_association")
    if (
        not isinstance(track_target, (int, float))
        or isinstance(track_target, bool)
        or not math.isfinite(float(track_target))
        or not 0.0 <= float(track_target) <= 0.001
    ):
        errors.append(
            "thresholds.json의 track_target_false_association은 0.001 이하여야 합니다."
        )
    return errors


def validate_person_model_contract(
    model_path: Path,
    contract_path: Path,
    target_class_ids: str,
) -> list[str]:
    """사람 탐지 가중치의 해시·클래스·전처리 계약을 배포 전에 검증한다."""

    errors: list[str] = []
    if not contract_path.is_file():
        return [f"사람 탐지 모델 계약 파일이 없습니다: {contract_path}"]
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return [f"사람 탐지 모델 계약을 JSON으로 읽을 수 없습니다: {contract_path}"]
    if not isinstance(contract, dict) or contract.get("schema_version") != 1:
        return ["사람 탐지 모델 계약 schema_version은 1이어야 합니다."]

    expected_hash = contract.get("model_sha256")
    if (
        not isinstance(expected_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_hash) is None
    ):
        errors.append("사람 탐지 모델 계약의 model_sha256이 올바르지 않습니다.")
    elif model_path.is_file() and _sha256_file(model_path) != expected_hash:
        errors.append("MODEL_PATH의 SHA-256이 사람 탐지 모델 계약과 다릅니다.")

    try:
        configured_classes = json.loads(target_class_ids)
    except json.JSONDecodeError:
        configured_classes = None
    if not isinstance(configured_classes, dict):
        errors.append("INFERENCE_TARGET_CLASS_IDS는 JSON object여야 합니다.")
    elif contract.get("target_class_ids") != configured_classes:
        errors.append("INFERENCE_TARGET_CLASS_IDS가 사람 탐지 모델 계약과 다릅니다.")

    preprocessing = contract.get("preprocessing_contract")
    if not isinstance(preprocessing, dict) or preprocessing.get("schema_version") != 1:
        errors.append("사람 탐지 모델의 전처리 계약이 올바르지 않습니다.")
    elif (
        preprocessing.get("label_derived") is not False
        or preprocessing.get("training_compatible") is not True
    ):
        errors.append("실제 추론에서 재현할 수 없는 사람 탐지 전처리 계약입니다.")
    elif preprocessing.get("inference_preprocessing_required") is not False:
        errors.append(
            "현재 worker는 필수 추론 전처리를 지원하지 않으므로 원본 프레임 모델이 필요합니다."
        )
    elif preprocessing.get("method") != ORIGINAL_FRAME_METHOD:
        errors.append("현재 worker가 지원하는 사람 탐지 입력은 원본 프레임뿐입니다.")
    return errors


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate(docker_root: Path) -> list[str]:
    errors: list[str] = []
    deep_env_path = docker_root / "env" / "deeplearning.dev.env"
    fastapi_env_path = docker_root / "env" / "fastapi.dev.env"
    worker_env_path = docker_root / "env" / "worker.dev.env"
    deep_env_exists = deep_env_path.is_file()
    fastapi_env_exists = fastapi_env_path.is_file()
    worker_env_exists = worker_env_path.is_file()
    deep_env: dict[str, str] = {}
    fastapi_env: dict[str, str] = {}
    worker_env: dict[str, str] = {}
    for path, destination in (
        (deep_env_path, deep_env),
        (worker_env_path, worker_env),
    ):
        if not path.is_file():
            errors.append(f"환경 파일이 없습니다: {path}")
            continue
        try:
            destination.update(read_env(path))
        except (OSError, UnicodeError, ValueError) as error:
            errors.append(str(error))

    # **fastapi.dev.env는 이 서버에 없는 것이 정상이다.** README.server.md가 두지
    # 말라고 명시한다 — fastapi는 개인 PC로 갔고(결정 0026) 여기서는 읽히지 않는데
    # MongoDB Atlas 접속 정보만 공용 장비에 남기 때문이다. 필수로 요구하면 그 문서를
    # 따르는 배포가 **전부 실패하고 롤백된다.** 있으면 교차 검사에 쓰고 없으면 넘긴다.
    if fastapi_env_exists:
        try:
            fastapi_env.update(read_env(fastapi_env_path))
        except (OSError, UnicodeError, ValueError) as error:
            errors.append(str(error))

    if deep_env_exists:
        for key in (
            "FACE_GALLERY_DATABASE_URL",
            "FACE_GALLERY_DATABASE_NAME",
            "FACE_RECOGNIZER",
            "FACE_RECOGNITION_MODEL_PATH",
            "FACE_RECOGNITION_MODEL_VERSION",
            "FACE_IDENTITY_THRESHOLD_FILE",
            "FACE_EMBEDDING_COLLECTION",
        ):
            if not deep_env.get(key, "").strip():
                errors.append(f"deeplearning.dev.env에 {key}가 필요합니다.")
        database_url = deep_env.get("FACE_GALLERY_DATABASE_URL", "").strip()
        if database_url and not database_url.startswith(
            ("mongodb://", "mongodb+srv://")
        ):
            errors.append("FACE_GALLERY_DATABASE_URL은 MongoDB URL이어야 합니다.")
        if deep_env.get("FACE_IDENTIFICATION_ENABLED", "").strip().lower() != "true":
            errors.append(
                "deeplearning.dev.env의 FACE_IDENTIFICATION_ENABLED는 true여야 합니다."
            )

    face_recognizer = deep_env.get("FACE_RECOGNIZER", "").strip().lower()
    face_model_config = FACE_MODEL_CONFIGS.get(face_recognizer)
    threshold_values: dict[str, object] | None = None
    if face_recognizer and face_model_config is None:
        errors.append("FACE_RECOGNIZER는 arcface 또는 adaface여야 합니다.")
    if face_model_config is not None:
        expected_metadata = face_model_config["metadata"]
        assert isinstance(expected_metadata, dict)
        if (
            deep_env.get("FACE_RECOGNITION_MODEL_VERSION", "").strip()
            != expected_metadata["model_version"]
        ):
            errors.append("FACE_RECOGNITION_MODEL_VERSION이 선택 모델 계약과 다릅니다.")
        if (
            deep_env.get("FACE_EMBEDDING_COLLECTION", "").strip()
            != face_model_config["collection"]
        ):
            errors.append("FACE_EMBEDDING_COLLECTION이 선택 모델과 다릅니다.")

        recognition_path = deep_env.get("FACE_RECOGNITION_MODEL_PATH", "").strip()
        expected_model_path = face_model_config["model_path"]
        if recognition_path and not recognition_path.replace("\\", "/").endswith(
            str(expected_model_path)
        ):
            # 파일이 있느냐와 별개로 **선택한 인식기의 가중치인지**를 본다.
            errors.append(
                f"FACE_RECOGNITION_MODEL_PATH가 선택 모델과 다릅니다. "
                f"{face_recognizer}는 .../{expected_model_path}를 써야 합니다."
            )
        if recognition_path:
            try:
                host_recognition_path = host_model_path(docker_root, recognition_path)
            except ValueError as error:
                errors.append(str(error))
            else:
                if (
                    not host_recognition_path.is_file()
                    or host_recognition_path.stat().st_size == 0
                ):
                    errors.append(
                        f"얼굴 인식 모델 파일이 없습니다: {host_recognition_path}"
                    )

        threshold_path = deep_env.get("FACE_IDENTITY_THRESHOLD_FILE", "").strip()
        if threshold_path:
            try:
                host_threshold_path = host_model_path(docker_root, threshold_path)
            except ValueError as error:
                errors.append(str(error))
            else:
                errors.extend(
                    validate_thresholds(host_threshold_path, expected_metadata)
                )
                try:
                    loaded_thresholds = json.loads(
                        host_threshold_path.read_text(encoding="utf-8")
                    )
                    if isinstance(loaded_thresholds, dict):
                        threshold_values = loaded_thresholds
                except (OSError, json.JSONDecodeError):
                    pass

    if fastapi_env_exists:
        fastapi_model = fastapi_env.get("FACE_RECOGNIZER", "").strip().lower()
        if fastapi_model != face_recognizer:
            errors.append(
                "fastapi.dev.env와 deeplearning.dev.env의 FACE_RECOGNIZER가 다릅니다."
            )
        if face_recognizer == "adaface":
            raw_fastapi_threshold = fastapi_env.get(
                "STUDENT_IDENTITY_CONFIDENCE_THRESHOLD_ADAFACE", ""
            ).strip()
            try:
                fastapi_threshold = float(raw_fastapi_threshold)
            except ValueError:
                errors.append(
                    "fastapi.dev.env에 AdaFace 학생 식별 임계값이 필요합니다."
                )
            else:
                selected_threshold = (
                    threshold_values.get("similarity_threshold")
                    if threshold_values is not None
                    else None
                )
                if not isinstance(selected_threshold, (int, float)) or not math.isclose(
                    fastapi_threshold,
                    float(selected_threshold),
                    rel_tol=0,
                    abs_tol=1e-12,
                ):
                    errors.append(
                        "FastAPI AdaFace 학생 식별 임계값이 thresholds.json과 다릅니다."
                    )

    required_worker_keys = (
        "STREAM_SOURCES",
        "MODEL_PATH",
        "MODEL_CONTRACT_PATH",
        "INFERENCE_DEVICE",
        "INFERENCE_TARGET_CLASS_IDS",
        "FASTAPI_URL",
        "FACE_IDENTITY_URL",
        "FACE_IDENTITY_CAMERA_IDS",
        "PERSON_TRACKING_CAMERA_IDS",
    )
    if worker_env_exists:
        for key in required_worker_keys:
            if not worker_env.get(key, "").strip():
                errors.append(f"worker.dev.env에 {key}가 필요합니다.")
    inference_device = worker_env.get("INFERENCE_DEVICE", "").strip()
    if inference_device and inference_device != "cuda":
        errors.append("GPU 서버의 INFERENCE_DEVICE는 cuda여야 합니다.")
    face_identity_url = worker_env.get("FACE_IDENTITY_URL", "").rstrip("/")
    if face_identity_url and face_identity_url != "http://deeplearning:8100":
        errors.append(
            "FACE_IDENTITY_URL은 compose 내부 주소 http://deeplearning:8100이어야 합니다."
        )
    fastapi_url = urlparse(worker_env.get("FASTAPI_URL", ""))
    if fastapi_url.scheme not in {"http", "https"} or not fastapi_url.netloc:
        errors.append("FASTAPI_URL이 올바른 HTTP URL이 아닙니다.")

    inference_threshold = worker_env.get("INFERENCE_CONFIDENCE_THRESHOLD", "").strip()
    if inference_threshold:
        try:
            if float(inference_threshold) >= 0.5:
                errors.append(
                    "INFERENCE_CONFIDENCE_THRESHOLD는 ByteTrack high(0.5)보다 낮아야 합니다."
                )
        except ValueError:
            errors.append("INFERENCE_CONFIDENCE_THRESHOLD가 숫자가 아닙니다.")

    camera_ids: set[str] = set()
    try:
        camera_ids = parse_camera_ids(worker_env.get("STREAM_SOURCES", ""))
    except ValueError as error:
        errors.append(str(error))
    face_camera_ids = {
        item.strip()
        for item in worker_env.get("FACE_IDENTITY_CAMERA_IDS", "").split(",")
        if item.strip()
    }
    missing_face_cameras = face_camera_ids - camera_ids
    if missing_face_cameras:
        errors.append(
            "FACE_IDENTITY_CAMERA_IDS가 STREAM_SOURCES에 모두 포함되지 않습니다."
        )
    if "classroom-cctv" not in camera_ids:
        errors.append("STREAM_SOURCES에 classroom-cctv 스트림이 필요합니다.")
    if not face_camera_ids or face_camera_ids == {"classroom-cctv"}:
        errors.append("교실 CCTV와 구분된 입구 얼굴 카메라 ID가 필요합니다.")

    tracking_ids = {
        item.strip()
        for item in worker_env.get("PERSON_TRACKING_CAMERA_IDS", "").split(",")
        if item.strip()
    }
    missing_tracking_cameras = tracking_ids - camera_ids
    if missing_tracking_cameras:
        errors.append(
            "PERSON_TRACKING_CAMERA_IDS가 STREAM_SOURCES에 모두 포함되지 않습니다."
        )
    if face_camera_ids & tracking_ids:
        errors.append("입구 얼굴 카메라와 사람 탐지 카메라 역할은 겹칠 수 없습니다.")
    if "classroom-cctv" not in tracking_ids:
        errors.append("PERSON_TRACKING_CAMERA_IDS에 classroom-cctv가 필요합니다.")
    unassigned_cameras = camera_ids - face_camera_ids - tracking_ids
    if unassigned_cameras:
        errors.append(
            "모든 STREAM_SOURCES는 얼굴 전용 또는 사람 탐지 역할이어야 합니다."
        )

    raw_routes = worker_env.get("IDENTITY_HANDOVER_ROUTES", "").strip()
    if raw_routes:
        try:
            routes = json.loads(raw_routes)
            if not isinstance(routes, list) or not routes:
                raise ValueError
            for route in routes:
                if not isinstance(route, dict):
                    raise TypeError
                entry_id = route.get("entry_camera_id")
                classroom_id = route.get("classroom_camera_id")
                zone = route.get("classroom_entry_zone")
                if (
                    entry_id not in face_camera_ids
                    or classroom_id not in tracking_ids
                    or not isinstance(zone, list)
                    or len(zone) != 4
                    or any(
                        not isinstance(value, (int, float))
                        or isinstance(value, bool)
                        or not 0.0 <= float(value) <= 1.0
                        for value in zone
                    )
                    or float(zone[0]) >= float(zone[2])
                    or float(zone[1]) >= float(zone[3])
                ):
                    raise ValueError
        except (TypeError, ValueError, json.JSONDecodeError):
            errors.append(
                "IDENTITY_HANDOVER_ROUTES의 카메라 또는 ROI 형식이 올바르지 않습니다."
            )

    for container_path in COMMON_FACE_MODEL_PATHS:
        path = host_model_path(docker_root, container_path)
        if not path.is_file() or path.stat().st_size == 0:
            errors.append(f"얼굴 모델 파일이 없습니다: {path}")

    worker_model = worker_env.get("MODEL_PATH", "").strip()
    resolved_worker_model: Path | None = None
    if worker_model:
        try:
            path = host_model_path(docker_root, worker_model)
        except ValueError as error:
            errors.append(str(error))
        else:
            if not path.is_file() or path.stat().st_size == 0:
                errors.append(f"객체 탐지 모델 파일이 없습니다: {path}")
            else:
                resolved_worker_model = path

    worker_contract = worker_env.get("MODEL_CONTRACT_PATH", "").strip()
    if worker_contract:
        try:
            contract_path = host_model_path(docker_root, worker_contract)
        except ValueError as error:
            errors.append(str(error))
        else:
            if resolved_worker_model is not None:
                errors.extend(
                    validate_person_model_contract(
                        resolved_worker_model,
                        contract_path,
                        worker_env.get("INFERENCE_TARGET_CLASS_IDS", ""),
                    )
                )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--docker-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help=".docker 디렉터리 경로",
    )
    args = parser.parse_args()
    errors = validate(args.docker_root.resolve())
    if errors:
        print("얼굴 식별 → 객체 추적 인계 배포 사전점검 실패:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("얼굴 식별 → 객체 추적 인계 배포 입력이 준비되었습니다.")
    print("환경변수 값과 자격 증명은 출력하지 않았습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
