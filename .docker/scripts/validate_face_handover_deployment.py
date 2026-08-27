#!/usr/bin/env python3
"""GPU 호스트의 얼굴 식별 → CCTV 신원 인계 배포 입력을 비밀 출력 없이 검증한다."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from urllib.parse import urlparse

from deployment_person_model_contract import verify_person_model_contract

EXPECTED_THRESHOLD_METADATA = {
    "model_name": "arcface",
    "model_version": "insightface-buffalo_l-w600k_r50-v0.7",
    "preprocessing_version": "insightface-norm-crop-112-v1",
}
FACE_MODEL_PATHS = (
    "/models/face/scrfd/scrfd_10g_bnkps.onnx",
    "/models/face/mediapipe/face_landmarker.task",
    "/models/face/buffalo_l/w600k_r50.onnx",
)
THRESHOLD_PATH = "/models/face/config/thresholds.json"


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


def parse_target_class_ids(raw_value: str) -> dict[int, str]:
    try:
        value = json.loads(raw_value)
        if not isinstance(value, dict) or not value:
            raise ValueError
        result = {int(key): name for key, name in value.items()}
        if any(
            key < 0 or not isinstance(name, str) or not name.strip()
            for key, name in result.items()
        ):
            raise ValueError
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(
            "INFERENCE_TARGET_CLASS_IDS가 올바른 JSON 객체가 아닙니다."
        ) from error
    return result


def validate_thresholds(path: Path) -> list[str]:
    errors: list[str] = []
    if not path.is_file():
        return [f"임계값 파일이 없습니다: {path}"]
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return [f"임계값 파일을 JSON으로 읽을 수 없습니다: {path}"]
    if not isinstance(values, dict):
        return [f"임계값 JSON 최상위 값은 object여야 합니다: {path}"]
    for key, expected in EXPECTED_THRESHOLD_METADATA.items():
        if values.get(key) != expected:
            errors.append(f"thresholds.json의 {key}가 현재 ArcFace 모델과 다릅니다.")
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
        or not 0.0 <= float(target_far) <= 1.0
    ):
        errors.append("thresholds.json의 target_far 범위가 올바르지 않습니다.")
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


def validate(docker_root: Path) -> list[str]:
    errors: list[str] = []
    deep_env_path = docker_root / "env" / "deeplearning.dev.env"
    worker_env_path = docker_root / "env" / "worker.dev.env"
    deep_env_exists = deep_env_path.is_file()
    worker_env_exists = worker_env_path.is_file()
    deep_env: dict[str, str] = {}
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

    if deep_env_exists:
        for key in ("FACE_GALLERY_DATABASE_URL", "FACE_GALLERY_DATABASE_NAME"):
            if not deep_env.get(key, "").strip():
                errors.append(f"deeplearning.dev.env에 {key}가 필요합니다.")
        database_url = deep_env.get("FACE_GALLERY_DATABASE_URL", "").strip()
        if database_url and not database_url.startswith(
            ("mongodb://", "mongodb+srv://")
        ):
            errors.append("FACE_GALLERY_DATABASE_URL은 MongoDB URL이어야 합니다.")

    required_worker_keys = (
        "STREAM_SOURCES",
        "MODEL_PATH",
        "MODEL_CONTRACT_PATH",
        "INFERENCE_DEVICE",
        "INFERENCE_IMAGE_SIZE",
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

    for container_path in FACE_MODEL_PATHS:
        path = host_model_path(docker_root, container_path)
        if not path.is_file() or path.stat().st_size == 0:
            errors.append(f"얼굴 모델 파일이 없습니다: {path}")
    errors.extend(validate_thresholds(host_model_path(docker_root, THRESHOLD_PATH)))

    worker_model = worker_env.get("MODEL_PATH", "").strip()
    worker_contract = worker_env.get("MODEL_CONTRACT_PATH", "").strip()
    raw_target_classes = worker_env.get("INFERENCE_TARGET_CLASS_IDS", "").strip()
    raw_image_size = worker_env.get("INFERENCE_IMAGE_SIZE", "").strip()
    if worker_model and worker_contract and raw_target_classes and raw_image_size:
        try:
            model_path = host_model_path(docker_root, worker_model)
            contract_path = host_model_path(docker_root, worker_contract)
            target_classes = parse_target_class_ids(raw_target_classes)
            image_size = int(raw_image_size)
            if not 320 <= image_size <= 4096:
                raise ValueError("INFERENCE_IMAGE_SIZE는 320~4096이어야 합니다.")
            verify_person_model_contract(
                str(model_path),
                str(contract_path),
                target_classes,
                image_size,
            )
        except (OSError, ValueError) as error:
            errors.append(str(error))
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
