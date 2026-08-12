"""스냅샷 조회의 계약.

핵심은 **"스냅샷 없음"과 "저장소 조회 실패"를 구분하는 것**이다. 둘을 같은 빈 응답으로
보여주면 운영자가 카메라 문제인지 저장소 문제인지 알 수 없다(결정 0011).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.shared.dependencies import get_snapshot_service
from app.snapshots.errors import SnapshotNotFoundError, SnapshotStorageUnavailableError
from app.snapshots.models import parse_snapshot_key
from app.snapshots.ports import ObjectContent, StoredObject
from app.snapshots.service import SnapshotService

JPEG = b"\xff\xd8fake-jpeg"


class FakeStorage:
    def __init__(self, keys: list[str] | None = None, *, fails: bool = False) -> None:
        self._keys = keys or []
        self._fails = fails

    def list_objects(self, prefix: str = "") -> Iterator[StoredObject]:
        if self._fails:
            raise SnapshotStorageUnavailableError()
        for key in sorted(self._keys):
            if key.startswith(prefix):
                yield StoredObject(key=key, size_bytes=len(JPEG), last_modified=datetime.now(UTC))

    def get_object(self, key: str) -> ObjectContent | None:
        if self._fails:
            raise SnapshotStorageUnavailableError()
        if key not in self._keys:
            return None
        return ObjectContent(data=JPEG, content_type="image/jpeg")


def build_service(keys: list[str] | None = None, *, fails: bool = False) -> SnapshotService:
    return SnapshotService(FakeStorage(keys, fails=fails))


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_snapshot_service, None)


# --- 객체 키 해석 -------------------------------------------------------------


def test_객체_키에서_카메라와_시각을_꺼낸다() -> None:
    parsed = parse_snapshot_key("camera-01/2026-08-12/20260812T090000Z.jpg")

    assert parsed is not None
    camera_id, captured_at = parsed
    assert camera_id == "camera-01"
    assert captured_at == datetime(2026, 8, 12, 9, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "key",
    [
        "camera-01/2026-08-12/20260812T090000Z.mp4",  # 영상은 스냅샷이 아니다
        "camera-01/20260812T090000Z.jpg",  # 날짜 디렉터리 없음
        "손으로-올린-파일.jpg",
        "../escape.jpg",
    ],
)
def test_규칙에_맞지_않는_키는_해석하지_않는다(key: str) -> None:
    assert parse_snapshot_key(key) is None


def test_규칙에_맞지_않는_객체가_섞여도_목록이_실패하지_않는다() -> None:
    service = build_service(
        [
            "camera-01/2026-08-12/20260812T090000Z.jpg",
            "손으로-올린-파일.jpg",
            "camera-01/2026-08-12/20260812T090000Z.mp4",
        ]
    )

    page = service.list_snapshots()

    assert page.total == 1


# --- 서비스 ------------------------------------------------------------------


def test_최신_촬영순으로_돌려준다() -> None:
    service = build_service(
        [
            "camera-01/2026-08-12/20260812T090000Z.jpg",
            "camera-01/2026-08-12/20260812T100000Z.jpg",
            "camera-02/2026-08-12/20260812T093000Z.jpg",
        ]
    )

    page = service.list_snapshots()

    assert [item.captured_at.hour for item in page.items] == [10, 9, 9]
    assert page.items[1].camera_id == "camera-02"


def test_카메라로_거른다() -> None:
    service = build_service(
        [
            "camera-01/2026-08-12/20260812T090000Z.jpg",
            "camera-02/2026-08-12/20260812T090000Z.jpg",
        ]
    )

    page = service.list_snapshots(camera_id="camera-02")

    assert page.total == 1
    assert page.items[0].camera_id == "camera-02"


def test_스냅샷이_없으면_빈_목록이다() -> None:
    """오류가 아니다. 정상 응답으로 0건을 돌려준다."""
    page = build_service([]).list_snapshots()

    assert page.items == []
    assert page.total == 0


def test_저장소가_죽으면_예외를_던진다() -> None:
    with pytest.raises(SnapshotStorageUnavailableError):
        build_service(fails=True).list_snapshots()


def test_규칙에_맞지_않는_키로는_이미지를_주지_않는다() -> None:
    """키 규칙 검사가 경로 조작을 막는 역할도 한다."""
    service = build_service(["camera-01/2026-08-12/20260812T090000Z.jpg"])

    with pytest.raises(SnapshotNotFoundError):
        service.get_image("../../etc/passwd")


def test_없는_스냅샷은_404다() -> None:
    service = build_service([])

    with pytest.raises(SnapshotNotFoundError):
        service.get_image("camera-01/2026-08-12/20260812T090000Z.jpg")


# --- HTTP --------------------------------------------------------------------


def test_목록_API는_items_total_limit_offset을_돌려준다(client: TestClient) -> None:
    app.dependency_overrides[get_snapshot_service] = lambda: build_service(
        ["camera-01/2026-08-12/20260812T090000Z.jpg"]
    )

    response = client.get("/api/v1/snapshots")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"items", "total", "limit", "offset"}
    assert body["total"] == 1
    assert body["items"][0]["camera_id"] == "camera-01"
    assert body["items"][0]["captured_at"].endswith("Z")


def test_이미지를_fastapi가_대신_전달한다(client: TestClient) -> None:
    key = "camera-01/2026-08-12/20260812T090000Z.jpg"
    app.dependency_overrides[get_snapshot_service] = lambda: build_service([key])

    response = client.get(f"/api/v1/snapshots/image/{key}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == JPEG


def test_저장소_장애는_503이다(client: TestClient) -> None:
    app.dependency_overrides[get_snapshot_service] = lambda: build_service(fails=True)

    response = client.get("/api/v1/snapshots")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SNAPSHOT_STORAGE_UNAVAILABLE"


def test_화면은_빈_상태와_조회_실패를_구분한다(client: TestClient) -> None:
    app.dependency_overrides[get_snapshot_service] = lambda: build_service([])
    empty = client.get("/snapshots")

    app.dependency_overrides[get_snapshot_service] = lambda: build_service(fails=True)
    broken = client.get("/snapshots")

    assert empty.status_code == 200
    assert "스냅샷이 없습니다" in empty.text
    # 화면은 200으로 뜨되 조회 실패를 분명히 알린다.
    assert broken.status_code == 200
    assert "조회하지 못했습니다" in broken.text
    assert "스냅샷이 없습니다" not in broken.text
