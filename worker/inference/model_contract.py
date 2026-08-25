"""사람 탐지 모델 파일과 추론 전처리 계약을 프로세스 시작 전에 검증한다."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

ORIGINAL_FRAME_METHOD = "original-frame-v1"


def verify_person_model_contract(
    model_path: str,
    contract_path: str | None,
    target_class_ids: dict[int, str],
) -> dict[str, Any] | None:
    """가중치 해시·클래스·전처리 계약이 현재 worker 실행 경로와 맞는지 확인한다."""

    if contract_path is None:
        return None
    model = Path(model_path).resolve(strict=True)
    contract_file = Path(contract_path).resolve(strict=True)
    try:
        raw = json.loads(contract_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("MODEL_CONTRACT_PATH의 JSON을 읽을 수 없습니다.") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("사람 탐지 모델 계약 schema_version은 1이어야 합니다.")

    expected_hash = raw.get("model_sha256")
    if (
        not isinstance(expected_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_hash) is None
    ):
        raise ValueError("사람 탐지 모델 계약의 model_sha256이 올바르지 않습니다.")
    actual_hash = _sha256_file(model)
    if actual_hash != expected_hash:
        raise ValueError("MODEL_PATH의 SHA-256이 모델 계약과 다릅니다.")

    configured_classes = {str(key): value for key, value in target_class_ids.items()}
    if raw.get("target_class_ids") != configured_classes:
        raise ValueError("INFERENCE_TARGET_CLASS_IDS가 모델 계약과 다릅니다.")

    preprocessing = raw.get("preprocessing_contract")
    if not isinstance(preprocessing, dict) or preprocessing.get("schema_version") != 1:
        raise ValueError("사람 탐지 모델의 전처리 계약이 올바르지 않습니다.")
    if (
        preprocessing.get("label_derived") is not False
        or preprocessing.get("training_compatible") is not True
    ):
        raise ValueError("실제 추론에서 재현할 수 없는 사람 탐지 전처리 계약입니다.")
    if preprocessing.get("inference_preprocessing_required") is not False:
        raise ValueError(
            "현재 worker는 필수 추론 전처리 어댑터가 없습니다. "
            "원본 프레임 모델 계약을 사용하세요."
        )
    if preprocessing.get("method") != ORIGINAL_FRAME_METHOD:
        raise ValueError("현재 worker가 지원하는 사람 탐지 입력은 원본 프레임뿐입니다.")
    return raw


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
