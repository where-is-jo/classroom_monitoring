from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import SettingsConfigDict
from shared.object_storage import ObjectStorageSettings

_INFERENCE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = _INFERENCE_DIR / "data"


class InferenceSettings(ObjectStorageSettings):
    """inference worker가 사용하는 설정.

    객체 저장소 값(`OBJECT_STORAGE_*`)은 `ObjectStorageSettings`에서 온다.
    recorder도 같은 mixin을 쓰므로 두 워커가 같은 변수를 같게 해석한다.
    """

    model_config = SettingsConfigDict(env_file=_INFERENCE_DIR / ".env", extra="ignore")

    # 다른 워커와 달리 필수가 아니다. 이 워커는 모든 값에 기본값이 있어 환경변수 없이도
    # 뜨고, 조립 실행에서는 프로세스 환경변수의 APP_ENV가 그대로 들어온다.
    app_env: Literal["local", "dev", "prod"] = "local"

    model_path: str = Field(default="yolov8n.pt")
    inference_device: Literal["cpu", "cuda"] = Field(default="cpu")
    inference_confidence_threshold: float = Field(default=0.25, ge=0.0, le=1.0)

    # --- 탐지 스냅샷 (결정 0011) ---
    # 영상 원본을 저장하지 않는 대신 탐지 시점 정지 이미지를 남긴다.
    # 저장은 명시적으로 켠다. recorder의 RECORDING_ENABLED가 기본 꺼짐인 것과 같다.
    snapshot_enabled: bool = False
    # 720p. 긴 변이 이 값을 넘을 때만 줄이고 확대는 하지 않는다.
    snapshot_max_long_side_px: int = Field(default=1280, ge=64, le=4096)
    snapshot_jpeg_quality: int = Field(default=80, ge=1, le=100)
    # 카메라당 최소 적재 간격. 탐지가 경계에서 떨릴 때 적재가 폭주하는 것을 막는다.
    # 용량 계산이 이 값에 기대고 있다(결정 0011).
    snapshot_min_interval_seconds: float = Field(default=60.0, gt=0, le=3600)

    @model_validator(mode="after")
    def _validate_environment_contract(self) -> Self:
        # 스냅샷을 끄면 저장소를 아예 만들지 않으므로 접속 설정을 요구하지 않는다.
        if self.snapshot_enabled:
            self.validate_object_storage(app_env=self.app_env)
        return self
