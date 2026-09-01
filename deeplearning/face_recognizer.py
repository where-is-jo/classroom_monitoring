"""ArcFace와 AdaFace를 같은 런타임 계약으로 선택·검증한다."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np

try:
    from .adaface_recognizer import AdaFaceOnnxRecognizer
    from .face_identification import FaceModelMetadata
except ImportError:  # 컨테이너는 deeplearning 디렉터리를 최상위 모듈 경로로 쓴다.
    from adaface_recognizer import AdaFaceOnnxRecognizer
    from face_identification import FaceModelMetadata

FaceRecognizerKind = Literal["arcface", "adaface"]

_MODEL_DEFAULTS: dict[FaceRecognizerKind, tuple[str, str, str, str]] = {
    "arcface": (
        "insightface-buffalo_l-w600k_r50-v0.7",
        "insightface-norm-crop-112-v1",
        "face_embeddings_arcface",
        "buffalo_l/w600k_r50.onnx",
    ),
    "adaface": (
        "cvlface-adaface-ir50-webface4m-fe7718c6",
        "cvlface-rgb-norm-crop-112-v1",
        "face_embeddings_adaface",
        "adaface/adaface_ir50_webface4m.onnx",
    ),
}


@dataclass(frozen=True)
class FaceRecognizerConfig:
    kind: FaceRecognizerKind
    model_path: Path
    metadata: FaceModelMetadata
    collection_name: str


def load_face_recognizer_config(
    environment: Mapping[str, str] | None = None,
    *,
    model_root: Path | None = None,
) -> FaceRecognizerConfig:
    """환경변수를 해석하고 모델·메타데이터·컬렉션 조합을 고정한다."""

    values = os.environ if environment is None else environment
    raw_kind = values.get("FACE_RECOGNIZER", "arcface").strip().lower()
    if raw_kind not in _MODEL_DEFAULTS:
        raise RuntimeError("FACE_RECOGNIZER는 arcface 또는 adaface여야 합니다.")
    kind: FaceRecognizerKind = raw_kind  # type: ignore[assignment]
    default_version, preprocessing, collection, relative_path = _MODEL_DEFAULTS[kind]

    configured_path = values.get("FACE_RECOGNITION_MODEL_PATH", "").strip()
    if configured_path:
        model_path = Path(configured_path)
    else:
        root = model_root or Path(__file__).resolve().parent / ".models"
        model_path = root / relative_path
    if not model_path.is_file() or model_path.stat().st_size == 0:
        raise RuntimeError("얼굴 인식 ONNX 모델 파일을 찾을 수 없습니다.")

    model_version = values.get(
        "FACE_RECOGNITION_MODEL_VERSION", default_version
    ).strip()
    if not model_version:
        raise RuntimeError("FACE_RECOGNITION_MODEL_VERSION이 비어 있습니다.")
    configured_collection = values.get("FACE_EMBEDDING_COLLECTION", collection).strip()
    if configured_collection != collection:
        raise RuntimeError(f"{kind} 모델은 {collection} 컬렉션만 사용할 수 있습니다.")
    return FaceRecognizerConfig(
        kind=kind,
        model_path=model_path,
        metadata=FaceModelMetadata(kind, model_version, preprocessing),
        collection_name=collection,
    )


def build_face_recognizer(
    config: FaceRecognizerConfig, *, providers: list[str] | None = None
) -> Any:
    """선택 모델을 CPU provider로 로드하고 실제 512차원 출력을 확인한다."""

    active_providers = providers or ["CPUExecutionProvider"]
    if config.kind == "arcface":
        from insightface.model_zoo import get_model

        recognizer = get_model(str(config.model_path), providers=active_providers)
    else:
        recognizer = AdaFaceOnnxRecognizer(
            config.model_path, providers=active_providers
        )
    if recognizer is None:
        raise RuntimeError("얼굴 인식 ONNX 모델을 로드하지 못했습니다.")
    recognizer.prepare(ctx_id=0 if active_providers[0].startswith("CUDA") else -1)
    _validate_recognizer_output(recognizer)
    return recognizer


def _validate_recognizer_output(recognizer: Any) -> None:
    sample = np.full((112, 112, 3), 127, dtype=np.uint8)
    try:
        vector = np.asarray(recognizer.get_feat(sample), dtype=np.float32).reshape(-1)
    except Exception as error:
        raise RuntimeError("얼굴 인식 모델의 시험 추론에 실패했습니다.") from error
    norm = float(np.linalg.norm(vector))
    if vector.size != 512 or not np.isfinite(vector).all() or norm <= 1e-12:
        raise RuntimeError("얼굴 인식 모델 출력은 유효한 512차원 벡터여야 합니다.")
