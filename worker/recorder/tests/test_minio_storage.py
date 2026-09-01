"""MinIO 어댑터의 예외 변환 검증.

실제 MinIO 서버 없이 돈다. 여기서 보는 것은 "SDK가 어떤 예외를 던지든 호출자는
ObjectStorageError만 본다"는 계약이다. 이게 깨지면 접속이 끊겼을 때 예외가 그대로
새어 나가 워커 스레드가 죽는다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shared.object_storage.minio import MinioObjectStorage
from shared.object_storage import ObjectStorageError

minio_error = pytest.importorskip(
    "minio.error", reason="minio 패키지가 없으면 예외 변환을 검증할 수 없다"
)
urllib3_exceptions = pytest.importorskip("urllib3.exceptions")


def make_connection_error() -> Exception:
    """MinIO가 꺼져 있을 때 실제로 올라오는 예외.

    urllib3.exceptions.MaxRetryError는 **OSError가 아니다.** 실제 서버로 확인했다.
    """
    return urllib3_exceptions.MaxRetryError(pool=None, url="/bucket", reason=None)


def make_server_error() -> Exception:
    """서버가 요청을 거절했을 때. 자격 증명 오류가 여기 해당한다."""
    return minio_error.S3Error(
        code="SignatureDoesNotMatch",
        message="The request signature we calculated does not match",
        resource="/bucket",
        request_id="req-1",
        host_id="host-1",
        response=None,
    )


class ExplodingClient:
    """정해진 예외를 던지는 MinIO 클라이언트 대역."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def bucket_exists(self, bucket: str) -> bool:
        raise self._error

    def make_bucket(self, bucket: str) -> None:
        raise self._error

    def fput_object(self, bucket: str, key: str, path: str, content_type: str) -> None:
        raise self._error

    def list_objects(self, bucket: str, prefix: str = "", recursive: bool = False) -> object:
        raise self._error

    def remove_object(self, bucket: str, key: str) -> None:
        raise self._error

    def stat_object(self, bucket: str, key: str) -> object:
        raise self._error


@pytest.fixture(params=["connection", "server"])
def failing_storage(request: pytest.FixtureRequest) -> MinioObjectStorage:
    error = make_connection_error() if request.param == "connection" else make_server_error()
    return MinioObjectStorage(ExplodingClient(error), "office-recordings")


def test_버킷_확인_실패를_바꿔_던진다(failing_storage: MinioObjectStorage) -> None:
    with pytest.raises(ObjectStorageError, match="버킷을 확인하지 못했습니다"):
        failing_storage.ensure_bucket()


def test_적재_실패를_바꿔_던진다(
    failing_storage: MinioObjectStorage, tmp_path: Path
) -> None:
    source = tmp_path / "seg.mp4"
    source.write_bytes(b"x")

    with pytest.raises(ObjectStorageError, match="객체를 저장하지 못했습니다"):
        failing_storage.put_object("camera-01/a.mp4", source)


def test_목록_실패를_바꿔_던진다(failing_storage: MinioObjectStorage) -> None:
    with pytest.raises(ObjectStorageError, match="객체 목록을 읽지 못했습니다"):
        list(failing_storage.list_objects())


def test_삭제_실패를_바꿔_던진다(failing_storage: MinioObjectStorage) -> None:
    with pytest.raises(ObjectStorageError, match="객체를 지우지 못했습니다"):
        failing_storage.remove_object("camera-01/a.mp4")


def test_접속_오류가_OSError가_아님을_고정한다() -> None:
    """이 사실이 바뀌면 _STORAGE_FAILURES에서 urllib3를 빼도 되는지 다시 본다."""
    assert not isinstance(make_connection_error(), OSError)


class RecordingClient:
    """호출을 기록만 하는 대역. 성공 경로의 인자 전달을 본다."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def bucket_exists(self, bucket: str) -> bool:
        self.calls.append(("bucket_exists", (bucket,)))
        return True

    def fput_object(self, bucket: str, key: str, path: str, content_type: str) -> None:
        self.calls.append(("fput_object", (bucket, key, path, content_type)))

    def stat_object(self, bucket: str, key: str) -> object:
        raise minio_error.InvalidResponseError(500, "text/plain", "boom")


def test_버킷이_있으면_만들지_않는다() -> None:
    client = RecordingClient()

    MinioObjectStorage(client, "office-recordings").ensure_bucket()

    assert [name for name, _ in client.calls] == ["bucket_exists"]


def test_mp4_content_type으로_올린다(tmp_path: Path) -> None:
    client = RecordingClient()
    source = tmp_path / "seg.mp4"
    source.write_bytes(b"video")

    stored = MinioObjectStorage(client, "office-recordings").put_object(
        "camera-01/2026-08-10/20260810T090000Z.mp4", source
    )

    _, args = client.calls[0]
    assert args[3] == "video/mp4"
    # stat_object가 실패해도 적재 자체는 끝났으므로 로컬 정보로 채운다.
    assert stored.size_bytes == len(b"video")
    assert stored.last_modified.tzinfo is not None


def test_예외_목록에_bare_Exception이_없다() -> None:
    """Exception을 잡으면 프로그래밍 오류까지 삼켜 버그를 가린다.

    minio와 urllib3의 import를 한 블록에 묶으면 한쪽이 없을 때 두 이름이 함께
    비어 이 목록이 (Exception, Exception, OSError)가 된다.
    """
    from shared.object_storage.minio import _STORAGE_FAILURES

    assert Exception not in _STORAGE_FAILURES
    assert BaseException not in _STORAGE_FAILURES
    assert OSError in _STORAGE_FAILURES
    assert urllib3_exceptions.HTTPError in _STORAGE_FAILURES
