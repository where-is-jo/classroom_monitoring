"""inference worker — 프레임에서 사람과 수화기를 탐지한다."""

from .config import InferenceSettings
from .model import Yolo8nDetector
from .processor import InferenceProcessor
from .types import Detection, Frame, InferenceResult
