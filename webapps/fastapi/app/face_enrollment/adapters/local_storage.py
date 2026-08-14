"""local 환경에서 수집 결과를 눈으로 확인하기 위한 파일 저장소."""

from __future__ import annotations

import hashlib
import io
import json
import random
import re
import shutil
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter

from ..models import FaceSampleMetadata

_RESAMPLING = Image.Resampling.LANCZOS


class LocalFaceObjectStorage:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._enrollment_dirs: dict[str, Path] = {}

    def prepare_enrollment(self, enrollment_id: str, student_id: str, created_at: datetime) -> None:
        self._validate_enrollment_id(enrollment_id)
        safe_student_id = self._safe_filename_component(student_id)
        folder_name = f"{created_at:%Y%m%d-%H%M%S}-{safe_student_id}"
        enrollment_dir = (self._root / folder_name).resolve()
        if enrollment_dir.parent != self._root:
            raise ValueError("허용되지 않은 얼굴 등록 경로입니다.")
        enrollment_dir.mkdir(parents=False, exist_ok=False)
        (enrollment_dir / "originals").mkdir()
        (enrollment_dir / "augmented").mkdir()
        self._enrollment_dirs[enrollment_id] = enrollment_dir

    def put_sample(self, enrollment_id: str, metadata: FaceSampleMetadata, content: bytes) -> None:
        enrollment_dir = self._safe_enrollment_dir(enrollment_id)
        originals_dir = (enrollment_dir / "originals").resolve()
        target = (originals_dir / f"{metadata.sample_id}.jpg").resolve()
        if target.parent != originals_dir:
            raise ValueError("허용되지 않은 얼굴 샘플 경로입니다.")
        target.write_bytes(content)
        metadata_target = target.with_suffix(".json")
        metadata_target.write_text(
            json.dumps(
                {
                    "sample_id": metadata.sample_id,
                    "pose": metadata.pose.value,
                    "captured_at": metadata.captured_at.isoformat(),
                    "quality": asdict(metadata.analysis),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def finalize_dataset(
        self, enrollment_id: str, student_id: str, augmented_sample_count: int
    ) -> None:
        enrollment_dir = self._safe_enrollment_dir(enrollment_id)
        originals_dir = enrollment_dir / "originals"
        augmented_dir = enrollment_dir / "augmented"
        original_paths = sorted(originals_dir.glob("*.jpg"))
        if not original_paths:
            raise ValueError("증강할 얼굴 원본이 없습니다.")

        manifest_samples: list[dict[str, object]] = [
            {
                "file": f"originals/{path.name}",
                "kind": "original",
                "source": None,
                "augmentation": None,
            }
            for path in original_paths
        ]
        for index in range(augmented_sample_count):
            source_path = original_paths[index % len(original_paths)]
            variant_number = index // len(original_paths) + 1
            augmented_name = f"{source_path.stem}_aug_{variant_number:02d}.jpg"
            augmented_path = augmented_dir / augmented_name
            seed = int.from_bytes(
                hashlib.sha256(
                    f"{enrollment_id}:{source_path.name}:{variant_number}".encode()
                ).digest()[:8]
            )
            recipe = self._augment(source_path, augmented_path, seed)
            manifest_samples.append(
                {
                    "file": f"augmented/{augmented_name}",
                    "kind": "augmented",
                    "source": f"originals/{source_path.name}",
                    "augmentation": recipe,
                }
            )

        manifest = {
            "dataset_version": "face-enrollment-v1",
            "enrollment_id": enrollment_id,
            "student_id": student_id,
            "original_sample_count": len(original_paths),
            "augmented_sample_count": augmented_sample_count,
            "total_sample_count": len(original_paths) + augmented_sample_count,
            "samples": manifest_samples,
        }
        (enrollment_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @staticmethod
    def _augment(source_path: Path, target_path: Path, seed: int) -> dict[str, object]:
        generator = random.Random(seed)
        recipe_index = generator.randrange(5)
        with Image.open(source_path) as source:
            image = source.convert("RGB")
            if recipe_index == 0:
                scale = generator.uniform(0.45, 0.7)
                reduced_size = (
                    max(16, round(image.width * scale)),
                    max(16, round(image.height * scale)),
                )
                image = image.resize(reduced_size, _RESAMPLING).resize(image.size, _RESAMPLING)
                recipe: dict[str, object] = {
                    "name": "resolution_and_jpeg",
                    "scale": round(scale, 3),
                }
            elif recipe_index == 1:
                brightness = generator.uniform(0.72, 1.28)
                contrast = generator.uniform(0.82, 1.18)
                image = ImageEnhance.Brightness(image).enhance(brightness)
                image = ImageEnhance.Contrast(image).enhance(contrast)
                recipe = {
                    "name": "lighting",
                    "brightness": round(brightness, 3),
                    "contrast": round(contrast, 3),
                }
            elif recipe_index == 2:
                radius = generator.uniform(0.4, 1.2)
                image = image.filter(ImageFilter.GaussianBlur(radius=radius))
                recipe = {"name": "gaussian_blur", "radius": round(radius, 3)}
            elif recipe_index == 3:
                angle = generator.uniform(-4.0, 4.0)
                image = image.rotate(
                    angle, resample=Image.Resampling.BICUBIC, fillcolor=(16, 24, 23)
                )
                recipe = {"name": "small_rotation", "angle_degrees": round(angle, 3)}
            else:
                color = generator.uniform(0.9, 1.1)
                contrast = generator.uniform(0.85, 1.15)
                image = ImageEnhance.Color(image).enhance(color)
                image = ImageEnhance.Contrast(image).enhance(contrast)
                recipe = {
                    "name": "camera_color",
                    "color": round(color, 3),
                    "contrast": round(contrast, 3),
                }

            jpeg_quality = generator.randint(62, 88)
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=jpeg_quality, optimize=True)
            target_path.write_bytes(buffer.getvalue())
            recipe["jpeg_quality"] = jpeg_quality
            return recipe

    def delete_enrollment(self, enrollment_id: str) -> None:
        enrollment_dir = self._safe_enrollment_dir(enrollment_id)
        if enrollment_dir.exists():
            shutil.rmtree(enrollment_dir)
        self._enrollment_dirs.pop(enrollment_id, None)

    def _safe_enrollment_dir(self, enrollment_id: str) -> Path:
        self._validate_enrollment_id(enrollment_id)
        enrollment_dir = self._enrollment_dirs.get(enrollment_id)
        if enrollment_dir is None:
            raise ValueError("준비되지 않은 얼굴 등록 저장소입니다.")
        return enrollment_dir

    @staticmethod
    def _validate_enrollment_id(enrollment_id: str) -> None:
        if not enrollment_id or any(
            character not in "0123456789abcdef-" for character in enrollment_id
        ):
            raise ValueError("허용되지 않은 얼굴 등록 식별자입니다.")

    @staticmethod
    def _safe_filename_component(value: str) -> str:
        normalized = re.sub(r"[^0-9A-Za-z가-힣._-]+", "-", value.strip())
        return normalized.strip(".-_")[:80] or "student"
