"""local 환경에서 수집 결과를 눈으로 확인하기 위한 파일 저장소."""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path


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
        self._enrollment_dirs[enrollment_id] = enrollment_dir

    def put_sample(self, enrollment_id: str, sample_id: str, content: bytes) -> None:
        enrollment_dir = self._safe_enrollment_dir(enrollment_id)
        enrollment_dir.mkdir(parents=True, exist_ok=True)
        target = (enrollment_dir / f"{sample_id}.jpg").resolve()
        if target.parent != enrollment_dir:
            raise ValueError("허용되지 않은 얼굴 샘플 경로입니다.")
        target.write_bytes(content)

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
