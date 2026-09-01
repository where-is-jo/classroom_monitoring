"""설정에서 객체 저장소를 만든다.

`recorder`와 `inference`가 같은 방식으로 저장소를 얻는다. 워커마다 조립을 따로
두면 한쪽만 고쳐졌을 때 두 워커가 다른 저장소를 보게 된다.

**MinIO SDK import는 `minio.py` 안에만 있다.** 이 파일은 백엔드가 minio일 때만
그 모듈을 부른다. local 백엔드로 도는 환경에 SDK가 없어도 기동한다.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .local import LocalObjectStorage
from .ports import ObjectStorage
from .settings import ObjectStorageSettings

logger = logging.getLogger(__name__)

__all__ = ["build_object_storage"]


def build_object_storage(
    settings: ObjectStorageSettings, *, local_fallback_dir: Path
) -> ObjectStorage:
    """설정에 맞는 객체 저장소를 만든다. SDK를 아는 곳은 어댑터뿐이다.

    `local_fallback_dir`은 `OBJECT_STORAGE_LOCAL_DIR`이 비었을 때 쓸 경로다.
    워커마다 데이터 디렉터리가 달라서 여기서 기본값을 정하지 않는다.
    """
    if settings.object_storage_backend == "local":
        root_dir = settings.object_storage_local_dir or local_fallback_dir
        logger.warning(
            "객체 저장소가 로컬 디렉터리다. 개발용이며 운영 보관 수단이 아니다: %s",
            root_dir,
        )
        return LocalObjectStorage(root_dir)

    # 검증이 세 값의 존재를 이미 보장한다.
    assert settings.object_storage_endpoint is not None
    assert settings.object_storage_access_key is not None
    assert settings.object_storage_secret_key is not None

    from .minio import MinioObjectStorage, build_minio_client

    client = build_minio_client(
        endpoint=settings.object_storage_endpoint,
        access_key=settings.object_storage_access_key.get_secret_value(),
        secret_key=settings.object_storage_secret_key.get_secret_value(),
        secure=settings.object_storage_secure,
        timeout_seconds=settings.object_storage_timeout_seconds,
    )
    storage = MinioObjectStorage(client, settings.object_storage_bucket)
    # 적재할 때가 되어서야 버킷이 없는 것을 알면 이미 놓친 것이 있다.
    storage.ensure_bucket()
    return storage
