from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .core import frame_id_from_record, read_jsonl, utc_now_iso, write_json
from .errors import AutoLabelingError


@dataclass(frozen=True)
class FrameQualityThresholds:
    """보수적으로 명백한 녹화·디코딩 실패만 차단하는 임계값."""

    dominant_green_fraction: float = 0.45
    dominant_magenta_fraction: float = 0.45
    near_black_fraction: float = 0.98
    near_white_fraction: float = 0.98
    flat_color_fraction: float = 0.60
    horizontal_band_pixel_fraction: float = 0.80
    horizontal_band_min_height_fraction: float = 0.08
    bottom_region_fraction: float = 0.25
    bottom_texture_ratio: float = 0.05
    reference_texture_variance: float = 500.0
    minimum_side_pixels: int = 64
    analysis_max_side_pixels: int = 256


def inspect_frame_quality(
    image_path: Path,
    thresholds: FrameQualityThresholds | None = None,
) -> dict[str, Any]:
    active = thresholds or FrameQualityThresholds()
    image = cv2.imread(str(image_path))
    if image is None:
        return {
            "passed": False,
            "reasons": ["decode-failure"],
            "width": 0,
            "height": 0,
        }

    height, width = image.shape[:2]
    analysis_image = image
    if max(width, height) > active.analysis_max_side_pixels:
        scale = active.analysis_max_side_pixels / max(width, height)
        analysis_image = cv2.resize(
            image,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    blue, green, red = (
        channel.astype(np.int16) for channel in cv2.split(analysis_image)
    )
    dominant_green = float(
        ((green >= 80) & (green >= red + 30) & (green >= blue + 30)).mean()
    )
    dominant_magenta = float(
        ((red >= 80) & (blue >= 80) & (red >= green + 30) & (blue >= green + 30)).mean()
    )
    near_black = float((analysis_image.max(axis=2) <= 10).mean())
    near_white = float((analysis_image.min(axis=2) >= 245).mean())
    green_mask = (green >= 80) & (green >= red + 30) & (green >= blue + 30)
    magenta_mask = (
        (red >= 80) & (blue >= 80) & (red >= green + 30) & (blue >= green + 30)
    )
    black_mask = analysis_image.max(axis=2) <= 10
    white_mask = analysis_image.min(axis=2) >= 245
    horizontal_bands = {
        "dominant-green-horizontal-band": _longest_row_band_fraction(
            green_mask, active.horizontal_band_pixel_fraction
        ),
        "dominant-magenta-horizontal-band": _longest_row_band_fraction(
            magenta_mask, active.horizontal_band_pixel_fraction
        ),
        "near-black-horizontal-band": _longest_row_band_fraction(
            black_mask, active.horizontal_band_pixel_fraction
        ),
        "near-white-horizontal-band": _longest_row_band_fraction(
            white_mask, active.horizontal_band_pixel_fraction
        ),
    }

    gray = cv2.cvtColor(analysis_image, cv2.COLOR_BGR2GRAY)
    bottom_start = max(1, round(len(gray) * (1 - active.bottom_region_fraction)))
    reference = gray[:bottom_start]
    bottom = gray[bottom_start:]
    reference_texture = float(cv2.Laplacian(reference, cv2.CV_32F).var())
    bottom_texture = float(cv2.Laplacian(bottom, cv2.CV_32F).var())
    bottom_texture_ratio = (
        bottom_texture / reference_texture if reference_texture > 0 else 1.0
    )

    # JPEG 노이즈를 무시하고 한 가지 색이 화면 대부분을 차지하는지 검사한다.
    quantized = (analysis_image.astype(np.uint16) >> 4).reshape(-1, 3)
    packed = (quantized[:, 0] << 8) | (quantized[:, 1] << 4) | quantized[:, 2]
    flat_color = float(np.bincount(packed, minlength=4096).max() / len(packed))

    reasons: list[str] = []
    if min(width, height) < active.minimum_side_pixels:
        reasons.append("image-too-small")
    if dominant_green >= active.dominant_green_fraction:
        reasons.append("dominant-green-corruption")
    if dominant_magenta >= active.dominant_magenta_fraction:
        reasons.append("dominant-magenta-corruption")
    if near_black >= active.near_black_fraction:
        reasons.append("near-black-frame")
    if near_white >= active.near_white_fraction:
        reasons.append("near-white-frame")
    if flat_color >= active.flat_color_fraction:
        reasons.append("flat-color-frame")
    reasons.extend(
        reason
        for reason, band_fraction in horizontal_bands.items()
        if band_fraction >= active.horizontal_band_min_height_fraction
    )
    if (
        reference_texture >= active.reference_texture_variance
        and bottom_texture_ratio <= active.bottom_texture_ratio
    ):
        reasons.append("bottom-texture-collapse")

    return {
        "passed": not reasons,
        "reasons": reasons,
        "width": width,
        "height": height,
        "dominant_green_fraction": round(dominant_green, 6),
        "dominant_magenta_fraction": round(dominant_magenta, 6),
        "near_black_fraction": round(near_black, 6),
        "near_white_fraction": round(near_white, 6),
        "flat_color_fraction": round(flat_color, 6),
        "horizontal_band_fractions": {
            reason: round(value, 6) for reason, value in horizontal_bands.items()
        },
        "reference_texture_variance": round(reference_texture, 6),
        "bottom_texture_variance": round(bottom_texture, 6),
        "bottom_texture_ratio": round(bottom_texture_ratio, 6),
    }


def _longest_row_band_fraction(mask: np.ndarray, row_fraction: float) -> float:
    qualifying = mask.mean(axis=1) >= row_fraction
    longest = 0
    current = 0
    for value in qualifying:
        if value:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest / len(qualifying) if len(qualifying) else 0.0


def scan_run_frame_quality(
    run_dir: Path,
    *,
    thresholds: FrameQualityThresholds | None = None,
    output_path: Path | None = None,
) -> dict[str, Any]:
    root = run_dir.resolve(strict=True)
    frames = read_jsonl(root / "frames.jsonl")
    active = thresholds or FrameQualityThresholds()
    failures: list[dict[str, object]] = []
    for frame in frames:
        frame_id = frame_id_from_record(frame)
        result = inspect_frame_quality(root / "frames" / f"{frame_id}.jpg", active)
        if result["passed"] is not True:
            failures.append(
                {
                    "frame_id": frame_id,
                    "session_id": frame.get("session_id"),
                    "source_id": frame.get("source_id"),
                    "timestamp_ms": frame.get("timestamp_ms"),
                    **result,
                }
            )
    report: dict[str, object] = {
        "schema_version": 1,
        "status": "passed" if not failures else "failed",
        "frame_count": len(frames),
        "passed_frame_count": len(frames) - len(failures),
        "failed_frame_count": len(failures),
        "thresholds": asdict(active),
        "failures": failures,
        "scanned_at": utc_now_iso(),
    }
    if output_path is not None:
        write_json(output_path, report)
    return report


def require_clean_frame(image_path: Path) -> dict[str, Any]:
    result = inspect_frame_quality(image_path)
    if result["passed"] is not True:
        raw_reasons = result.get("reasons")
        if not isinstance(raw_reasons, list):
            raise AutoLabelingError("프레임 품질 검사 사유가 올바르지 않습니다.")
        reasons = ", ".join(str(value) for value in raw_reasons)
        raise AutoLabelingError(
            f"손상·저품질 프레임입니다: {image_path.name}: {reasons}"
        )
    return result
