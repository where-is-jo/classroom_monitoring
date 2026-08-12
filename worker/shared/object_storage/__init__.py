"""객체 저장소 경계. 두 워커 이상이 함께 쓴다.

`recorder`가 영상 세그먼트를, `inference`가 탐지 스냅샷을 여기로 올린다.
어느 한쪽에 두면 다른 쪽이 그 워커를 import하게 되어 워커 사이에 의존이 생긴다
(`shared/README.md`의 "워커는 서로를 import하지 않는다").

**외부 SDK import는 `minio.py` 안에만 있다.** 다른 곳에서는 `ObjectStorage` 포트만 본다.
"""

from __future__ import annotations

from .errors import ObjectStorageError
from .local import LocalObjectStorage
from .ports import ObjectStorage, StoredObject
from .settings import ObjectStorageSettings

__all__ = [
    "LocalObjectStorage",
    "ObjectStorage",
    "ObjectStorageError",
    "ObjectStorageSettings",
    "StoredObject",
]
