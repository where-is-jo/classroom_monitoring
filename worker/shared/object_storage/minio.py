"""MinIO 객체 저장소 어댑터.

**S3 호환 API 범위 안에서만 쓴다**(결정 0004). MinIO 고유 기능에 의존하면 나중에
실제 S3나 다른 호환 저장소로 옮길 수 없다. 여기서 쓰는 것은 put/list/remove와
버킷 존재 확인뿐이다.

MinIO SDK import는 이 파일에만 있다. 다른 곳에서는 ObjectStorage 포트만 본다.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import ObjectStorageError
from .ports import StoredObject

logger = logging.getLogger(__name__)

# 두 import를 따로 감싼다. 한 블록에 묶으면 minio가 없을 때 urllib3 이름까지 함께
# 비어, 아래 예외 목록이 bare Exception이 되어 모든 오류를 삼킨다.
try:
    from minio import Minio
    from minio.error import MinioException
except ImportError:  # pragma: no cover - 패키지가 없는 환경에서의 경로
    Minio = None  # type: ignore[assignment, misc]
    MinioException = None  # type: ignore[assignment, misc]

try:
    import urllib3
    from urllib3.exceptions import HTTPError as Urllib3HTTPError
except ImportError:  # pragma: no cover - 패키지가 없는 환경에서의 경로
    urllib3 = None  # type: ignore[assignment]
    Urllib3HTTPError = None  # type: ignore[assignment, misc]

# 저장소 호출이 실패하는 세 갈래를 모두 잡아 ObjectStorageError로 바꾼다.
# - MinioException: S3Error·ServerError·InvalidResponseError의 상위. 서버가 거절한 경우
# - Urllib3HTTPError: MaxRetryError 등 접속 자체가 안 된 경우. **OSError가 아니다.**
#   이걸 빠뜨리면 MinIO가 꺼져 있을 때 예외가 그대로 새어 나가 워커 스레드가 죽는다.
#   실제 서버를 내린 상태로 확인한 사실이다.
# - OSError: 올릴 로컬 파일을 읽지 못한 경우
# 프로그래밍 오류(TypeError 등)는 그대로 올려 보낸다. 감추면 버그를 가린다.
_STORAGE_FAILURES: tuple[type[BaseException], ...] = tuple(
    error_type
    for error_type in (MinioException, Urllib3HTTPError, OSError)
    if error_type is not None
)

DEFAULT_CONTENT_TYPE = "video/mp4"

# 적재는 호출한 스레드를 막는다. inference의 스냅샷 적재는 추론 소비자 스레드에서
# 일어나므로, MinIO가 죽어 있을 때 SDK 기본 재시도(초 단위로 여러 번)를 그대로 두면
# 그동안 프레임이 계속 버려진다. 짧게 끊고 실패로 넘긴다.
_DEFAULT_TIMEOUT_SECONDS = 5.0
_DEFAULT_RETRIES = 1


def build_minio_client(
    *,
    endpoint: str,
    access_key: str,
    secret_key: str,
    secure: bool,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    retries: int = _DEFAULT_RETRIES,
) -> Any:
    """MinIO 클라이언트를 만든다. 패키지가 없으면 무엇을 설치할지 알린다."""
    if Minio is None:
        raise ObjectStorageError(
            "minio 패키지가 설치되어 있지 않습니다. "
            "해당 워커의 requirements.txt의 의존성을 설치하세요."
        )

    http_client = None
    if urllib3 is not None:
        http_client = urllib3.PoolManager(
            timeout=urllib3.util.Timeout(
                connect=timeout_seconds, read=timeout_seconds
            ),
            retries=urllib3.util.Retry(total=retries, backoff_factor=0.2),
        )
    return Minio(
        endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=secure,
        http_client=http_client,
    )


class MinioObjectStorage:
    """MinIO 버킷 하나를 객체 저장소로 쓴다."""

    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    @property
    def bucket(self) -> str:
        return self._bucket

    def ensure_bucket(self) -> None:
        """버킷이 없으면 만든다. 시작 시점에 한 번 부른다.

        적재할 때가 되어서야 버킷이 없는 것을 알면, 그때는 이미 세그먼트가
        쌓이고 있다.
        """
        try:
            if not self._client.bucket_exists(self._bucket):
                self._client.make_bucket(self._bucket)
                logger.info("버킷을 만들었다: %s", self._bucket)
        except _STORAGE_FAILURES as error:
            # 주소와 사유는 남기되 자격 증명은 남기지 않는다.
            raise ObjectStorageError(
                f"버킷을 확인하지 못했습니다: {self._bucket} ({error})"
            ) from error

    def put_object(
        self, key: str, source_path: Path, *, content_type: str = DEFAULT_CONTENT_TYPE
    ) -> StoredObject:
        try:
            self._client.fput_object(
                self._bucket, key, str(source_path), content_type=content_type
            )
        except _STORAGE_FAILURES as error:
            raise ObjectStorageError(f"객체를 저장하지 못했습니다: {key} ({error})") from error

        return self._stored_or_fallback(key, source_path.stat().st_size)

    def put_bytes(self, key: str, data: bytes, *, content_type: str) -> StoredObject:
        try:
            self._client.put_object(
                self._bucket,
                key,
                io.BytesIO(data),
                length=len(data),
                content_type=content_type,
            )
        except _STORAGE_FAILURES as error:
            raise ObjectStorageError(f"객체를 저장하지 못했습니다: {key} ({error})") from error

        return self._stored_or_fallback(key, len(data))

    def _stored_or_fallback(self, key: str, size_bytes: int) -> StoredObject:
        stored = self._stat_object(key)
        if stored is not None:
            return stored
        # 조회에 실패해도 적재 자체는 끝났다. 아는 정보로 채운다.
        return StoredObject(key=key, size_bytes=size_bytes, last_modified=datetime.now(UTC))

    def list_objects(self, prefix: str = "") -> Iterator[StoredObject]:
        try:
            for item in self._client.list_objects(
                self._bucket, prefix=prefix, recursive=True
            ):
                last_modified = item.last_modified
                if last_modified is not None and last_modified.tzinfo is None:
                    last_modified = last_modified.replace(tzinfo=UTC)
                yield StoredObject(
                    key=item.object_name,
                    size_bytes=item.size or 0,
                    last_modified=last_modified,
                )
        except _STORAGE_FAILURES as error:
            raise ObjectStorageError(f"객체 목록을 읽지 못했습니다: {error}") from error

    def remove_object(self, key: str) -> None:
        try:
            self._client.remove_object(self._bucket, key)
        except _STORAGE_FAILURES as error:
            raise ObjectStorageError(f"객체를 지우지 못했습니다: {key} ({error})") from error

    def _stat_object(self, key: str) -> StoredObject | None:
        try:
            stat = self._client.stat_object(self._bucket, key)
        except _STORAGE_FAILURES:
            return None

        last_modified = stat.last_modified
        if last_modified is not None and last_modified.tzinfo is None:
            last_modified = last_modified.replace(tzinfo=UTC)
        return StoredObject(
            key=key, size_bytes=stat.size or 0, last_modified=last_modified
        )
