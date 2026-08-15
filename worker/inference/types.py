"""추론 입출력 타입.

Frame은 워커마다 따로 정의하지 않고 shared에서 가져온다. 같은 것에 이름이
둘 생기면 stream이 넘긴 배열과 inference가 기대하는 배열이 같은 것인지
타입만 보고 알 수 없다.
"""

from __future__ import annotations

from dataclasses import dataclass

from shared.types import Frame

BBox = tuple[int, int, int, int]

__all__ = ["BBox", "Detection", "Frame", "InferenceResult"]


@dataclass(frozen=True)
class Detection:
    class_id: int
    class_name: str
    confidence: float
    bbox: BBox
    # 얼굴 식별 모델이 연결되면 채우는 선택 필드다. 사람 탐지만 수행하는 현재
    # detector는 기본값을 그대로 사용한다. 학생 이름·좌석 상태는 이 경계에 두지 않는다.
    student_id: str | None = None
    identity_confidence: float | None = None
    face_bbox: BBox | None = None


@dataclass(frozen=True)
class InferenceResult:
    frame_shape: tuple[int, int, int]
    detections: tuple[Detection, ...]
