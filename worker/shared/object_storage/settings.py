"""객체 저장소 설정. 워커들이 같은 변수를 같은 방식으로 읽는다.

`recorder`와 `inference`가 각자 `OBJECT_STORAGE_*`를 정의하면 같은 환경변수가
워커에 따라 다르게 해석될 수 있다. `shared/camera_sources.py`가 `STREAM_SOURCES`를
공용화한 것과 같은 이유다.

**`BaseSettings`를 상속한 mixin이다.** 워커 설정 클래스가 함께 상속해서 쓴다.
`env_file`은 상속하는 쪽이 정한다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings

# S3 버킷 이름 규칙. MinIO SDK도 같은 규칙으로 검사하지만 첫 호출 시점에 ValueError를
# 던진다. 그때는 이미 적재할 것이 쌓이는 중이라, 시작 시점에 미리 걸러낸다.
_BUCKET_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")

__all__ = ["ObjectStorageSettings"]


class ObjectStorageSettings(BaseSettings):
    """객체 저장소 접속 설정."""

    # local은 MinIO 없이 적재 경로를 확인하기 위한 개발용이다. 운영 보관 수단이 아니다.
    object_storage_backend: Literal["local", "minio"] = "local"
    object_storage_bucket: str = "classroom-snapshots"
    object_storage_local_dir: Path | None = None

    # MinIO 접속 정보는 비밀값이라 기본값을 주지 않는다.
    object_storage_endpoint: str | None = None
    object_storage_access_key: SecretStr | None = None
    object_storage_secret_key: SecretStr | None = None
    object_storage_secure: bool = True
    object_storage_timeout_seconds: float = Field(default=5.0, gt=0, le=60)

    # .env.example은 경로 항목을 비워 둔다. 빈 문자열이 그대로 오면 Path(".")가 되어
    # 실행 위치에 객체가 쌓인다. 비어 있으면 None으로 두고 조립 시점의 기본값을 쓰게 한다.
    # 기본 경로는 워커마다 달라서(recorder/data, inference/data) 여기서 정하지 않는다.
    @field_validator("object_storage_local_dir", mode="before")
    @classmethod
    def _blank_object_dir_is_unset(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("object_storage_bucket")
    @classmethod
    def _validate_bucket_name(cls, value: str) -> str:
        if not _BUCKET_NAME_PATTERN.match(value):
            raise ValueError(
                "버킷 이름은 소문자·숫자·하이픈·점으로 3~63자여야 하고 "
                "소문자나 숫자로 시작하고 끝나야 합니다."
            )
        return value

    def validate_object_storage(self, *, app_env: str) -> None:
        """접속에 필요한 값이 갖춰졌는지 확인한다.

        `model_validator`로 두지 않은 이유는 `app_env`가 이 mixin이 아니라 워커 설정에
        있기 때문이다. 워커의 `model_validator`가 이 메서드를 부른다.
        """
        if self.object_storage_backend == "minio":
            missing_names = [
                name
                for name, value in (
                    ("OBJECT_STORAGE_ENDPOINT", self.object_storage_endpoint),
                    ("OBJECT_STORAGE_ACCESS_KEY", self.object_storage_access_key),
                    ("OBJECT_STORAGE_SECRET_KEY", self.object_storage_secret_key),
                )
                if value is None or not str(value).strip()
            ]
            if missing_names:
                raise ValueError(
                    "OBJECT_STORAGE_BACKEND=minio에 필요한 환경변수가 없습니다: "
                    + ", ".join(missing_names)
                )

        # 로컬 디렉터리는 운영 보관 수단이 아니다. 결정 0004가 기각한 방식이다.
        if app_env == "prod" and self.object_storage_backend == "local":
            raise ValueError(
                "APP_ENV=prod에서는 OBJECT_STORAGE_BACKEND=local을 쓸 수 없습니다. "
                "로컬 디렉터리는 개발용이며 보존 기간·접근 권한을 분리할 수 없습니다."
            )
