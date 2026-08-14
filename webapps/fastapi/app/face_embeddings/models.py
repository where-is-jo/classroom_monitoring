"""얼굴 embedding 컬렉션의 영속 문서 모델."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class FaceEmbedding:
    id: str
    student_id: str
    student_name: str
    student_number: str
    enrollment_id: str
    vector: tuple[float, ...]
    dimension: int
    normalized: bool
    model_name: str
    model_version: str
    preprocessing_version: str
    source_sample_count: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class SampleEmbedding:
    vector: tuple[float, ...]
    dimension: int
    normalized: bool
    model_name: str
    model_version: str
    preprocessing_version: str
