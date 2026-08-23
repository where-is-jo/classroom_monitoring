from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .errors import AutoLabelingError

SAFE_ID_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
ALLOWED_SUBJECT_CATEGORIES = {"synthetic", "consenting-adult", "student"}
REQUIRED_CONSENT_SCOPE = "person-detection-training"


@dataclass(frozen=True)
class Settings:
    sampling_interval_seconds: float
    jpeg_quality: int
    sampling_policy_version: str
    candidate_confidence_threshold: float
    overlap_review_iou_threshold: float
    review_sample_fraction: float
    review_sample_min_frames: int
    review_time_bucket_seconds: int
    calibration_match_iou_threshold: float
    calibration_target_precision: float
    calibration_target_recall: float
    calibration_min_frames: int
    calibration_min_sessions: int
    deduplication_policy_version: str
    duplicate_phash_hamming_threshold: int
    duplicate_pixel_mae_threshold: float
    duplicate_bbox_iou_threshold: float
    duplicate_comparison_size: int


@dataclass(frozen=True)
class SourceInput:
    source_id: str
    file_path: Path
    approval_reference: str
    consent_scope: str
    retention_expires_at: str
    camera_id: str
    session_id: str
    captured_at: str
    subject_category: str
    usage: str
    requested_split: str | None


@dataclass(frozen=True)
class InputManifest:
    run_id: str
    sources: tuple[SourceInput, ...]
    manifest_role: str


@dataclass(frozen=True)
class FrameRecord:
    frame_id: str
    source_id: str
    source_sha256: str
    timestamp_ms: int
    camera_id: str
    session_id: str
    captured_at: str
    approval_reference: str
    consent_scope: str
    retention_expires_at: str
    subject_category: str
    usage: str
    requested_split: str | None
    image_path: str
    image_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidateBox:
    class_id: int
    class_name: str
    confidence: float
    bbox_xyxy_pixels: tuple[float, float, float, float]
    bbox_yolo: tuple[float, float, float, float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_settings(path: Path | None = None) -> Settings:
    settings_path = path or Path(__file__).with_name("config") / "settings.yml"
    try:
        raw = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise AutoLabelingError("자동 라벨링 설정을 읽을 수 없습니다.") from exc
    if not isinstance(raw, dict):
        raise AutoLabelingError("자동 라벨링 설정은 YAML 객체여야 합니다.")
    try:
        settings = Settings(**raw)
    except TypeError as exc:
        raise AutoLabelingError("자동 라벨링 설정 필드가 계약과 다릅니다.") from exc
    _validate_settings(settings)
    return settings


def _validate_settings(settings: Settings) -> None:
    if (
        not math.isfinite(settings.sampling_interval_seconds)
        or settings.sampling_interval_seconds <= 0
    ):
        raise AutoLabelingError("프레임 추출 간격은 0보다 커야 합니다.")
    if not 1 <= settings.jpeg_quality <= 100:
        raise AutoLabelingError("JPEG 품질은 1~100이어야 합니다.")
    probability_values = (
        settings.candidate_confidence_threshold,
        settings.overlap_review_iou_threshold,
        settings.review_sample_fraction,
        settings.calibration_match_iou_threshold,
        settings.calibration_target_precision,
        settings.calibration_target_recall,
        settings.duplicate_pixel_mae_threshold,
        settings.duplicate_bbox_iou_threshold,
    )
    if any(
        not math.isfinite(value) or value < 0 or value > 1
        for value in probability_values
    ):
        raise AutoLabelingError("신뢰도·IoU·표본 비율 설정은 0~1이어야 합니다.")
    if settings.review_sample_min_frames < 1:
        raise AutoLabelingError("최소 검수 표본 수는 1 이상이어야 합니다.")
    if settings.review_time_bucket_seconds < 1:
        raise AutoLabelingError("검수 시간 구간은 1초 이상이어야 합니다.")
    if settings.calibration_min_frames < 1 or settings.calibration_min_sessions < 1:
        raise AutoLabelingError("보정 최소 프레임·세션 수는 1 이상이어야 합니다.")
    if not settings.deduplication_policy_version.strip():
        raise AutoLabelingError("중복 제거 정책 버전은 비어 있을 수 없습니다.")
    if not 0 <= settings.duplicate_phash_hamming_threshold <= 64:
        raise AutoLabelingError("pHash Hamming 임계값은 0~64여야 합니다.")
    if settings.duplicate_comparison_size < 8:
        raise AutoLabelingError("중복 비교 이미지 크기는 8 이상이어야 합니다.")


def load_input_manifest(
    path: Path,
    *,
    now: datetime | None = None,
    allow_approved_student_data: bool = False,
) -> InputManifest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AutoLabelingError("입력 manifest를 읽을 수 없습니다.") from exc
    if not isinstance(raw, dict):
        raise AutoLabelingError("입력 manifest 최상위는 JSON 객체여야 합니다.")
    run_id = _require_safe_id(raw.get("run_id"), "run_id")
    manifest_role = raw.get("manifest_role", "dataset")
    if manifest_role != "dataset":
        raise AutoLabelingError("prepare 입력 manifest_role은 dataset이어야 합니다.")
    raw_sources = raw.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise AutoLabelingError("sources는 한 개 이상의 항목이 있는 배열이어야 합니다.")
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None:
        raise AutoLabelingError("현재 시각에는 timezone이 있어야 합니다.")
    sources = tuple(
        _parse_source(
            item,
            path.parent,
            current_time,
            allow_approved_student_data=allow_approved_student_data,
        )
        for item in raw_sources
    )
    source_ids = [source.source_id for source in sources]
    if len(source_ids) != len(set(source_ids)):
        raise AutoLabelingError("source_id는 manifest 안에서 중복될 수 없습니다.")
    return InputManifest(run_id=run_id, sources=sources, manifest_role=manifest_role)


def _parse_source(
    raw: Any,
    manifest_dir: Path,
    now: datetime,
    *,
    allow_approved_student_data: bool,
) -> SourceInput:
    if not isinstance(raw, dict):
        raise AutoLabelingError("sources 항목은 JSON 객체여야 합니다.")
    source_id = _require_safe_id(raw.get("source_id"), "source_id")
    camera_id = _require_safe_id(raw.get("camera_id"), "camera_id")
    session_id = _require_safe_id(raw.get("session_id"), "session_id")
    approval_reference = _require_text(
        raw.get("approval_reference"), "approval_reference"
    )
    consent_scope = _require_text(raw.get("consent_scope"), "consent_scope")
    if consent_scope != REQUIRED_CONSENT_SCOPE:
        raise AutoLabelingError(
            f"source_id={source_id}: consent_scope는 {REQUIRED_CONSENT_SCOPE}이어야 합니다."
        )
    subject_category = _require_text(raw.get("subject_category"), "subject_category")
    if subject_category not in ALLOWED_SUBJECT_CATEGORIES:
        raise AutoLabelingError(
            f"source_id={source_id}: 허용되지 않은 subject_category입니다."
        )
    if subject_category == "student" and not allow_approved_student_data:
        raise AutoLabelingError(
            f"source_id={source_id}: 실제 학생 영상은 "
            "--allow-approved-student-data가 필요합니다."
        )
    usage = raw.get("usage", "dataset")
    if usage != "dataset":
        raise AutoLabelingError(
            f"source_id={source_id}: prepare는 dataset 입력만 받습니다."
        )
    requested_split = raw.get("requested_split")
    if requested_split is not None and requested_split not in {"train", "val"}:
        raise AutoLabelingError(
            f"source_id={source_id}: requested_split은 train 또는 val이어야 합니다."
        )
    captured_at = _parse_aware_datetime(raw.get("captured_at"), "captured_at")
    retention_expires_at = _parse_aware_datetime(
        raw.get("retention_expires_at"), "retention_expires_at"
    )
    if retention_expires_at <= now:
        raise AutoLabelingError(f"source_id={source_id}: 보존 만료 시각이 지났습니다.")
    raw_path = _require_text(raw.get("file_path"), "file_path")
    file_path = Path(raw_path)
    if not file_path.is_absolute():
        file_path = manifest_dir / file_path
    try:
        file_path = file_path.resolve(strict=True)
    except OSError as exc:
        raise AutoLabelingError(
            f"source_id={source_id}: 입력 파일을 찾을 수 없습니다."
        ) from exc
    if not file_path.is_file() or file_path.suffix.lower() != ".mp4":
        raise AutoLabelingError(
            f"source_id={source_id}: MP4 파일만 처리할 수 있습니다."
        )
    return SourceInput(
        source_id=source_id,
        file_path=file_path,
        approval_reference=approval_reference,
        consent_scope=consent_scope,
        retention_expires_at=retention_expires_at.isoformat(),
        camera_id=camera_id,
        session_id=session_id,
        captured_at=captured_at.isoformat(),
        subject_category=subject_category,
        usage=usage,
        requested_split=requested_split,
    )


def _require_safe_id(value: Any, field_name: str) -> str:
    text = _require_text(value, field_name)
    if not SAFE_ID_PATTERN.fullmatch(text):
        raise AutoLabelingError(
            f"{field_name}는 영문·숫자로 시작하고 영문·숫자·점·밑줄·하이픈만 써야 합니다."
        )
    return text


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AutoLabelingError(f"{field_name}은 비어 있지 않은 문자열이어야 합니다.")
    return value.strip()


def _parse_aware_datetime(value: Any, field_name: str) -> datetime:
    text = _require_text(value, field_name)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AutoLabelingError(f"{field_name}은 ISO 8601 시각이어야 합니다.") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AutoLabelingError(f"{field_name}에는 timezone이 있어야 합니다.")
    return parsed


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as file_handle:
            for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise AutoLabelingError("파일 해시를 계산할 수 없습니다.") from exc
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def stable_frame_id(
    source_sha256: str, timestamp_ms: int, sampling_policy_version: str
) -> str:
    value = f"{source_sha256}:{timestamp_ms}:{sampling_policy_version}".encode()
    return sha256_bytes(value)[:24]


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AutoLabelingError(f"{path.name}을 읽을 수 없습니다.") from exc


def write_jsonl(path: Path, values: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    with temporary_path.open("w", encoding="utf-8", newline="\n") as file_handle:
        for value in values:
            file_handle.write(
                json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n"
            )
    temporary_path.replace(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise AutoLabelingError(f"{path.name}을 읽을 수 없습니다.") from exc
    values: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AutoLabelingError(
                f"{path.name} {line_number}번째 줄이 올바른 JSON이 아닙니다."
            ) from exc
        if not isinstance(value, dict):
            raise AutoLabelingError(f"{path.name} 항목은 JSON 객체여야 합니다.")
        values.append(value)
    return values


def frame_id_from_record(frame: dict[str, Any]) -> str:
    frame_id = frame.get("frame_id")
    if not isinstance(frame_id, str) or not SAFE_ID_PATTERN.fullmatch(frame_id):
        raise AutoLabelingError("frames.jsonl에 올바른 frame_id가 없습니다.")
    return frame_id


def verified_frame_image_path(run_dir: Path, frame: dict[str, Any]) -> Path:
    frame_id = frame_id_from_record(frame)
    expected_relative_path = f"frames/{frame_id}.jpg"
    if frame.get("image_path") != expected_relative_path:
        raise AutoLabelingError(f"frame_id={frame_id}: 이미지 경로가 계약과 다릅니다.")
    image_sha256 = frame.get("image_sha256")
    if not isinstance(image_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", image_sha256
    ):
        raise AutoLabelingError(
            f"frame_id={frame_id}: 이미지 SHA-256이 올바르지 않습니다."
        )
    image_path = run_dir / expected_relative_path
    if not image_path.is_file() or image_path.is_symlink():
        raise AutoLabelingError(f"frame_id={frame_id}: 이미지가 없거나 링크입니다.")
    if sha256_file(image_path) != image_sha256:
        raise AutoLabelingError(f"frame_id={frame_id}: 추출 뒤 이미지가 변경됐습니다.")
    return image_path
