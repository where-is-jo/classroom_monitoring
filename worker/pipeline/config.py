"""stream과 inference를 한 프로세스로 조립할 때만 쓰는 설정."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict
from shared.settings_sources import customise_sources_with_yaml

_PIPELINE_DIR = Path(__file__).resolve().parent

_APP_ENV_FOR_FILE_SELECTION = os.environ.get("APP_ENV", "local")

# 조립 실행에서는 이 파일 하나만 읽는다. 워커별 .env.*를 각각 읽게 두면
# 같은 변수가 두 파일에 흩어져 어느 값이 적용됐는지 알 수 없게 된다.
# stream/inference의 "환경 의존 설정·비밀값"(APP_ENV, STREAM_SOURCES, MODEL_PATH,
# INFERENCE_DEVICE, OBJECT_STORAGE_* 등)만 여기 담긴다 — 일반 설정·판정 기준값은
# 각 워커 자신의 config/settings.yml을 그대로 읽는다.
PIPELINE_ENV_FILE = _PIPELINE_DIR / f".env.{_APP_ENV_FOR_FILE_SELECTION}"


class PipelineSettings(BaseSettings):
    """버퍼와 소비자 동작을 정하는 값. 전부 환경과 무관하게 같은 값이라 yml에만 둔다."""

    model_config = SettingsConfigDict(
        env_file=PIPELINE_ENV_FILE,
        yaml_file=_PIPELINE_DIR / "config" / "settings.yml",
        # PyYAML의 기본 파일 인코딩은 OS 로캘을 따른다. 한국어 Windows에서는 cp949라
        # yml의 한국어 주석을 읽다가 UnicodeDecodeError가 난다. 명시적으로 고정한다.
        yaml_file_encoding="utf-8",
        extra="ignore",
    )

    # 기본값이 1인 이유는 실시간 파이프라인에서 최신 한 장이면 충분하기 때문이다.
    # 2 이상은 추론 시간이 들쭉날쭉할 때 그 편차를 흡수하려는 경우에만 올린다.
    # 키울수록 오래된 프레임을 들고 있게 되어 지연이 커진다.
    frame_buffer_maxsize: int = Field(default=1, ge=1, le=32)

    # 버퍼가 비어 있어도 종료 신호를 확인할 수 있어야 해서 대기에 상한을 둔다.
    inference_poll_timeout_seconds: float = Field(default=0.5, gt=0, le=10)

    # 연속으로 이만큼 추론에 실패하면 파이프라인을 멈춘다. 계속 실패하는 상태로
    # 도는 것은 프레임을 버리면서 아무것도 만들지 않는 것과 같다.
    inference_max_consecutive_failures: int = Field(default=5, ge=1, le=100)

    # 탐지 결과를 전송할 FastAPI URL. 조립 실행에서 worker는 HTTP POST로 여기에
    # 적재한다. 웹앱이 다른 주소로 뜨면 이 값만 바꾸면 된다.
    # 환경마다 다른 주소이므로 .env.{APP_ENV} 쪽에 둔다 — settings.yml에 넣지 않는다.
    fastapi_url: str = Field(default="http://127.0.0.1:8001")

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
            settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
        )
