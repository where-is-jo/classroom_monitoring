"""inference worker 설정. 환경변수·yml에서 읽고 프로세스 시작 시 검증한다.

값은 두 곳에서 온다.

- `.env.{APP_ENV}` — 환경마다 달라야 하는 값과 비밀값(`MODEL_PATH`,
  `MODEL_CONTRACT_PATH`, `INFERENCE_DEVICE`, `OBJECT_STORAGE_BACKEND`와 MinIO 접속
  정보). 커밋하지 않는다.
- `config/settings.yml` — 환경과 무관하게 같은 값. 커밋한다.

우선순위는 실제 OS 환경변수 > `.env.{APP_ENV}` > `config/settings.yml`이다
(`shared.settings_sources.customise_sources_with_yaml`).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from shared.object_storage import ObjectStorageSettings
from shared.settings_sources import customise_sources_with_yaml

_INFERENCE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = _INFERENCE_DIR / "data"

_APP_ENV_FOR_FILE_SELECTION = os.environ.get("APP_ENV", "local")


class InferenceSettings(ObjectStorageSettings):
    """inference worker가 사용하는 설정.

    객체 저장소 값(`OBJECT_STORAGE_*`)은 `ObjectStorageSettings`에서 온다.
    recorder도 같은 mixin을 쓰므로 두 워커가 같은 변수를 같게 해석한다.
    `OBJECT_STORAGE_BACKEND`·`OBJECT_STORAGE_ENDPOINT`·`ACCESS_KEY`·`SECRET_KEY`는
    환경마다 실제로 달라지는 값이라 `.env.*`에 두고, 나머지(`BUCKET`·`LOCAL_DIR`·
    `SECURE`·`TIMEOUT_SECONDS`)는 `config/settings.yml`에 둔다.
    """

    model_config = SettingsConfigDict(
        env_file=_INFERENCE_DIR / f".env.{_APP_ENV_FOR_FILE_SELECTION}",
        yaml_file=_INFERENCE_DIR / "config" / "settings.yml",
        # PyYAML의 기본 파일 인코딩은 OS 로캘을 따른다. 한국어 Windows에서는 cp949라
        # yml의 한국어 주석을 읽다가 UnicodeDecodeError가 난다. 명시적으로 고정한다.
        yaml_file_encoding="utf-8",
        extra="ignore",
    )

    # 다른 워커와 달리 필수가 아니다. 이 워커는 모든 값에 기본값이 있어 환경변수 없이도
    # 뜨고, 조립 실행에서는 프로세스 환경변수의 APP_ENV가 그대로 들어온다.
    app_env: Literal["local", "dev", "prod"] = "local"

    model_path: str = Field(default="yolo11m.pt")
    model_contract_path: str | None = Field(default=None)
    inference_device: Literal["cpu", "cuda"] = Field(default="cpu")
    inference_confidence_threshold: float = Field(default=0.25, ge=0.0, le=1.0)
    # 모델에 넣기 전에 프레임의 긴 변을 이 크기로 맞춘다.
    #
    # **지정하지 않으면 ultralytics가 640으로 줄인다.** 3A컴퓨터실 CCTV는 1280x1944라
    # 3분의 1로 축소되고, 뒤쪽에 앉은 사람이 뭉개져 탐지에서 빠진다. 실측에서 640과
    # 1280의 차이는 신뢰도 0.6 이상 탐지가 프레임당 4.4명과 7.6명이었다.
    # 1280을 넘기면 탐지 수는 더 늘지 않고 처리 시간만 배로 늘었다.
    inference_image_size: int = Field(default=1280, ge=320, le=4096)

    # 모델이 내놓는 클래스 중 어떤 것을 탐지로 볼지. `{클래스 번호: 이름}`이다.
    #
    # **모델을 바꾸면 이 값도 함께 바꿔야 한다.** 클래스 번호는 모델마다 다르다.
    # COCO로 학습한 범용 모델은 person이 0, cell phone이 67이지만, 사람만 학습한
    # 전용 모델은 클래스가 0 하나뿐이다. 그런 모델에 67을 요구하면 ultralytics가
    # 존재하지 않는 클래스를 거르게 되고, 무엇이 빠졌는지 로그에 남지 않는다.
    #
    # 그래서 코드 상수가 아니라 설정으로 둔다 — 모델 교체가 이미지 재빌드를 부르지
    # 않아야 한다. 환경변수로는 JSON으로 준다:
    #   INFERENCE_TARGET_CLASS_IDS={"0": "person"}
    inference_target_class_ids: dict[int, str] = Field(
        default_factory=lambda: {0: "person", 67: "cell phone"}
    )

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

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return customise_sources_with_yaml(
            settings_cls,
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
        )

    @model_validator(mode="after")
    def _validate_environment_contract(self) -> Self:
        if self.app_env in {"dev", "prod"} and not self.model_contract_path:
            raise ValueError("dev/prod에서는 MODEL_CONTRACT_PATH가 필요합니다.")
        if not self.inference_target_class_ids:
            raise ValueError(
                "INFERENCE_TARGET_CLASS_IDS는 한 클래스 이상이어야 합니다."
            )
        if any(
            class_id < 0 or not class_name.strip()
            for class_id, class_name in self.inference_target_class_ids.items()
        ):
            raise ValueError(
                "INFERENCE_TARGET_CLASS_IDS의 번호는 0 이상이고 이름은 비어 있지 않아야 합니다."
            )
        # 스냅샷을 끄면 저장소를 아예 만들지 않으므로 접속 설정을 요구하지 않는다.
        if self.snapshot_enabled:
            self.validate_object_storage(app_env=self.app_env)
        return self
