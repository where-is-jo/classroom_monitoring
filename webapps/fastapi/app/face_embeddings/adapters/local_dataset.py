"""로컬 얼굴 등록 데이터셋 조회 어댑터."""

import json
from pathlib import Path

from ..errors import FaceEmbeddingInputError


class LocalFaceDatasetReader:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def read_originals(
        self, *, enrollment_id: str, student_id: str, student_number: str
    ) -> list[bytes]:
        candidates: list[tuple[Path, dict[str, object]]] = []
        for manifest_path in self._root.glob("*/manifest.json"):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            manifest_student = manifest.get("student_id")
            manifest_enrollment = manifest.get("enrollment_id")
            if manifest_enrollment == enrollment_id or manifest_student in {
                student_id,
                student_number,
            }:
                candidates.append((manifest_path, manifest))
        if not candidates:
            raise FaceEmbeddingInputError("학생과 연결된 얼굴 원본 이미지가 없습니다.")
        manifest_path, manifest = max(candidates, key=lambda item: item[0].parent.stat().st_mtime)
        samples = manifest.get("samples")
        if not isinstance(samples, list):
            raise FaceEmbeddingInputError("얼굴 데이터셋 manifest가 올바르지 않습니다.")
        images: list[bytes] = []
        dataset_dir = manifest_path.parent.resolve()
        for sample in samples:
            if not isinstance(sample, dict) or sample.get("kind") != "original":
                continue
            relative = sample.get("file")
            if not isinstance(relative, str):
                continue
            path = (dataset_dir / relative).resolve()
            if dataset_dir not in path.parents or not path.is_file():
                continue
            images.append(path.read_bytes())
        if not images:
            raise FaceEmbeddingInputError("벡터화할 얼굴 원본 이미지가 없습니다.")
        return images
