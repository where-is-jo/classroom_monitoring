"""MinIO 스냅샷 저장소 어댑터.

**MinIO SDK import는 이 파일에만 있다.** 다른 곳에서는 `SnapshotStorage` 포트만 본다.
S3 호환 API 범위 안에서만 쓴다(결정 0004) — 여기서 쓰는 것은 list와 get뿐이다.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from ..errors import SnapshotStorageUnavailableError
from ..ports import ObjectContent, StoredObject

logger = logging.getLogger(__name__)

# 두 import를 따로 감싼다. 한 블록에 묶으면 minio가 없을 때 urllib3 이름까지 함께 비어,
# 아래 예외 목록이 bare Exception이 되어 모든 오류를 삼킨다.
try:
    from minio import Minio
    from minio.error import MinioException, S3Error
except ImportError:  # pragma: no cover - 패키지가 없는 환경에서의 경로
    # 이름에 None을 대입하는 것은 mypy가 보기엔 타입 자리에 값을 넣는 것이라 오류다.
    # 여기서는 "패키지가 없으면 이름이 비어 있다"가 의도된 동작이므로 억제한다.
    # worker/inference/model.py가 ultralytics에 쓰는 것과 같은 패턴이다.
    Minio = None  # type: ignore[assignment, misc]
    MinioException = None  # type: ignore[assignment, misc]
    S3Error = None  # type: ignore[assignment, misc]

try:
    import urllib3
    from urllib3.exceptions import HTTPError as Urllib3HTTPError
except ImportError:  # pragma: no cover
    urllib3 = None  # type: ignore[assignment]
    Urllib3HTTPError = None  # type: ignore[assignment, misc]

# Urllib3HTTPError를 빠뜨리면 MinIO가 꺼져 있을 때 예외가 그대로 새어 나가
# 500이 된다. OSError가 아니라서 잡히지 않는다.
_STORAGE_FAILURES: tuple[type[BaseException], ...] = tuple(
    error_type
    for error_type in (MinioException, Urllib3HTTPError, OSError)
    if error_type is not None
)

_NO_SUCH_KEY_CODES = frozenset({"NoSuchKey", "NoSuchBucket"})


def build_minio_client(
    *,
    endpoint: str,
    access_key: str,
    secret_key: str,
    secure: bool,
    timeout_seconds: float = 5.0,
) -> Any:
    """MinIO 클라이언트를 만든다. 패키지가 없으면 무엇을 설치할지 알린다."""
    if Minio is None:
        raise RuntimeError(
            "minio 패키지가 설치되어 있지 않습니다. requirements.txt의 의존성을 설치하세요."
        )

    http_client = None
    if urllib3 is not None:
        # 요청 처리 중에 저장소가 죽으면 워커 스레드가 오래 묶인다. 짧게 끊는다.
        http_client = urllib3.PoolManager(
            timeout=urllib3.util.Timeout(connect=timeout_seconds, read=timeout_seconds),
            retries=urllib3.util.Retry(total=1, backoff_factor=0.2),
        )
    return Minio(
        endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=secure,
        http_client=http_client,
    )


class MinioSnapshotStorage:
    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def list_objects(self, prefix: str = "") -> Iterator[StoredObject]:
        try:
            for item in self._client.list_objects(self._bucket, prefix=prefix, recursive=True):
                last_modified = item.last_modified
                if last_modified is not None and last_modified.tzinfo is None:
                    last_modified = last_modified.replace(tzinfo=UTC)
                yield StoredObject(
                    key=item.object_name,
                    size_bytes=item.size or 0,
                    last_modified=last_modified or datetime.now(UTC),
                )
        except _STORAGE_FAILURES as error:
            logger.warning("스냅샷 목록을 읽지 못했다: %s", error)
            raise SnapshotStorageUnavailableError() from error

    def get_object(self, key: str) -> ObjectContent | None:
        response = None
        try:
            response = self._client.get_object(self._bucket, key)
            data = response.read()
            content_type = response.headers.get("Content-Type", "image/jpeg")
            return ObjectContent(data=data, content_type=content_type)
        except _STORAGE_FAILURES as error:
            # 없는 객체는 장애가 아니다. 둘을 섞으면 화면이 503을 띄운다.
            if (
                S3Error is not None
                and isinstance(error, S3Error)
                and getattr(error, "code", None) in _NO_SUCH_KEY_CODES
            ):
                return None
            logger.warning("스냅샷을 읽지 못했다: %s", error)
            raise SnapshotStorageUnavailableError() from error
        finally:
            if response is not None:
                response.close()
                response.release_conn()
