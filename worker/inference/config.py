from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_INFERENCE_DIR = Path(__file__).resolve().parent


class InferenceSettings(BaseSettings):
    """inference worker가 사용하는 설정."""

    model_config = SettingsConfigDict(env_file=_INFERENCE_DIR / ".env", extra="ignore")

    model_path: str = Field(default="yolov8n.pt")
    inference_device: Literal["cpu", "cuda"] = Field(default="cpu")
    inference_confidence_threshold: float = Field(default=0.25, ge=0.0, le=1.0)
