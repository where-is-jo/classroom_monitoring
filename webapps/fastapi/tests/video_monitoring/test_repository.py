"""video_monitoring repository 어댑터 계약 (memory/Mongo real-only parity)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from app.shared.errors import RepositoryUnavailableError
from app.video_monitoring.adapters.memory_playback_session_repository import (
    MemoryPlaybackSessionRepository,
)
from app.video_monitoring.adapters.memory_repository import MemoryVideoStreamRepository
from app.video_monitoring.adapters.mongo_playback_session_repository import (
    MongoPlaybackSessionRepository,
)
from app.video_monitoring.adapters.mongo_repository import MongoVideoStreamRepository
from app.video_monitoring.models import (
    PlaybackKind,
    PlaybackSession,
    PlaybackSessionStatus,
    VideoStream,
)

NOW = datetime(2026, 8, 14, 3, 0, tzinfo=UTC)


def make_stream(
    *,
    stream_id: str,
    camera_id: str,
    enabled: bool = True,
    is_demo: bool = False,
) -> VideoStream:
    return VideoStream(
        id=stream_id,
        camera_id=camera_id,
        classroom_id="classroom-a101",
        camera_label="A101 전면 카메라",
        playback_kind=PlaybackKind.WEBRTC,
        playback_path=f"/webrtc/{camera_id}",
        enabled=enabled,
        last_frame_at=None,
        last_detection_at=None,
        is_demo=is_demo,
        created_at=NOW,
        updated_at=NOW,
    )


# ── memory ────────────────────────────────────────────────────────────────────


def test_memory_find_monitoring_streams_parity() -> None:
    repository = MemoryVideoStreamRepository()
    for stream in (
        make_stream(stream_id="stream-01", camera_id="camera-01"),
        make_stream(stream_id="stream-02", camera_id="camera-02", is_demo=True),
        make_stream(stream_id="stream-03", camera_id="camera-03", enabled=False),
        make_stream(stream_id="stream-04", camera_id="camera-04", enabled=False, is_demo=True),
    ):
        repository.save(stream)

    assert {item.camera_id for item in repository.find_monitoring_streams()} == {"camera-01"}
    assert {item.camera_id for item in repository.find_all_enabled()} == {
        "camera-01",
        "camera-02",
    }


def test_memory_playback_session_repository_roundtrip() -> None:
    repository = MemoryPlaybackSessionRepository()
    session = _session("session-01")

    assert repository.find_by_id("session-01") is None
    assert repository.delete_by_id("session-01") is False

    repository.save(session)
    assert repository.find_by_id("session-01") == session
    assert repository.delete_by_id("session-01") is True
    assert repository.find_by_id("session-01") is None


def test_memory_playback_session_repository_replace() -> None:
    repository = MemoryPlaybackSessionRepository()
    session = _session("session-01")

    repository.save(session)
    repository.save(PlaybackSession(**{**session.__dict__, "status": PlaybackSessionStatus.ACTIVE}))

    restored = repository.find_by_id("session-01")
    assert restored is not None
    assert restored.status == PlaybackSessionStatus.ACTIVE


# ── mongo (fake collection, 외부 서비스 없음) ────────────────────────────────


class RecordingMongoCollection:
    """find(query).sort()와 index 기록만 지원하는 fake collection."""

    def __init__(self, documents: list[dict[str, object]] | None = None) -> None:
        self.documents: list[dict[str, object]] = list(documents or [])
        self.indexes: list[tuple[list[tuple[str, int]], dict[str, object]]] = []
        self.queries: list[dict[str, object]] = []
        self.updates: list[tuple[dict[str, object], dict[str, object]]] = []
        self.replace_calls: list[dict[str, object]] = []
        self.deleted: list[dict[str, object]] = []

    def create_index(self, fields: list[tuple[str, int]], **options: object) -> None:
        self.indexes.append((fields, options))

    def find(self, query: dict[str, object]) -> _Cursor:
        self.queries.append(query)
        return _Cursor(
            [
                document
                for document in self.documents
                if all(document.get(key) == value for key, value in query.items())
            ]
        )

    def find_one(self, query: dict[str, object]) -> dict[str, object] | None:
        return next(
            (
                document
                for document in self.documents
                if all(document.get(key) == value for key, value in query.items())
            ),
            None,
        )

    def replace_one(
        self,
        query: dict[str, object],
        replacement: dict[str, object],
        *,
        upsert: bool,
    ) -> None:
        del upsert
        self.replace_calls.append(replacement)
        for index, document in enumerate(self.documents):
            if document.get("_id") == query.get("_id"):
                self.documents[index] = replacement
                return
        self.documents.append(replacement)

    def update_one(self, query: dict[str, object], update: dict[str, object]) -> None:
        self.updates.append((query, update))

    def delete_one(self, query: dict[str, object]) -> object:
        before = len(self.documents)
        self.deleted.append(query)
        self.documents = [
            document for document in self.documents if document.get("_id") != query.get("_id")
        ]
        return _DeleteResult(before - len(self.documents))


class _DeleteResult:
    def __init__(self, deleted_count: int) -> None:
        self.deleted_count = deleted_count


class _Cursor:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self._documents = documents

    def sort(self, key: str, direction: int) -> _Cursor:
        self._documents = sorted(
            self._documents,
            key=lambda document: str(document.get(key, "")),
            reverse=direction == -1,
        )
        return self

    def __iter__(self) -> Iterator[dict[str, object]]:
        return iter(self._documents)


class FakeMongoDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, RecordingMongoCollection] = {}

    def __getitem__(self, name: str) -> RecordingMongoCollection:
        return self.collections.setdefault(name, RecordingMongoCollection())


def _session(
    session_id: str, status: PlaybackSessionStatus = PlaybackSessionStatus.CREATED
) -> PlaybackSession:
    return PlaybackSession(
        session_id=session_id,
        stream_id="stream-01",
        camera_id="camera-01",
        status=status,
        owner_token_hash="hash",
        expires_at=NOW + timedelta(seconds=300),
        remote_resource_location=None,
        created_at=NOW,
        updated_at=NOW,
    )


def test_mongo_find_monitoring_streams_query_parity() -> None:
    database = FakeMongoDatabase()
    database["video_streams"].documents = [
        MongoVideoStreamRepository._to_document(
            make_stream(stream_id="stream-01", camera_id="camera-01")
        ),
        MongoVideoStreamRepository._to_document(
            make_stream(stream_id="stream-02", camera_id="camera-02", is_demo=True)
        ),
        MongoVideoStreamRepository._to_document(
            make_stream(stream_id="stream-03", camera_id="camera-03", enabled=False)
        ),
        MongoVideoStreamRepository._to_document(
            make_stream(stream_id="stream-04", camera_id="camera-04", enabled=False, is_demo=True)
        ),
    ]
    repository = MongoVideoStreamRepository(database)  # type: ignore[arg-type]
    collection = database["video_streams"]

    result = repository.find_monitoring_streams()

    assert {item.camera_id for item in result} == {"camera-01"}
    assert collection.queries[-1] == {"enabled": True, "is_demo": False}


def test_mongo_playback_session_repository_roundtrip() -> None:
    database = FakeMongoDatabase()
    repository = MongoPlaybackSessionRepository(database)  # type: ignore[arg-type]
    session = _session("session-01")

    assert repository.find_by_id("session-01") is None
    assert repository.delete_by_id("session-01") is False

    repository.save(session)
    assert repository.find_by_id("session-01") == session

    collection = database["playback_sessions"]
    # 존재 여부 확인용 첫 delete(False)도 fake collection의 delete 기록을 남긴다.
    collection.deleted.clear()
    assert repository.delete_by_id("session-01") is True
    assert collection.deleted == [{"_id": "session-01"}]
    assert repository.find_by_id("session-01") is None


def test_mongo_playback_session_save_preserves_remote_location() -> None:
    database = FakeMongoDatabase()
    repository = MongoPlaybackSessionRepository(database)  # type: ignore[arg-type]
    session = PlaybackSession(
        **{
            **_session("session-01").__dict__,
            "status": PlaybackSessionStatus.ACTIVE,
            "remote_resource_location": "http://127.0.0.1:8889/webrtc/camera-01/whep",
        }
    )

    repository.save(session)
    restored = repository.find_by_id("session-01")

    assert restored is not None
    assert restored.status == PlaybackSessionStatus.ACTIVE
    assert restored.remote_resource_location == ("http://127.0.0.1:8889/webrtc/camera-01/whep")


def test_mongo_playback_session_ensure_indexes() -> None:
    database = FakeMongoDatabase()
    MongoPlaybackSessionRepository.ensure_indexes(database)  # type: ignore[arg-type]

    collection = database["playback_sessions"]
    assert any(fields == [("expires_at", 1)] for fields, _options in collection.indexes)


class FailingCollection:
    """PyMongoError를 흉내 내는 collection."""

    def __init__(self, error: Exception) -> None:
        self._error = error

    def find(self, query: dict[str, object]) -> object:
        del query
        raise self._error

    def find_one(self, query: dict[str, object]) -> object:
        del query
        raise self._error

    def replace_one(
        self, query: dict[str, object], replacement: dict[str, object], *, upsert: bool
    ) -> object:
        del query, replacement, upsert
        raise self._error

    def delete_one(self, query: dict[str, object]) -> object:
        del query
        raise self._error


class FailingDatabase:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def __getitem__(self, name: str) -> FailingCollection:
        del name
        return FailingCollection(self._error)


@pytest.fixture
def failing_database() -> FailingDatabase:
    import pymongo.errors

    return FailingDatabase(pymongo.errors.PyMongoError("boom"))


def test_mongo_find_monitoring_streams_failure_raises_unavailable(
    failing_database: FailingDatabase,
) -> None:
    repository = MongoVideoStreamRepository(failing_database)  # type: ignore[arg-type]
    with pytest.raises(RepositoryUnavailableError):
        repository.find_monitoring_streams()


def test_mongo_playback_save_failure_raises_unavailable(
    failing_database: FailingDatabase,
) -> None:
    repository = MongoPlaybackSessionRepository(failing_database)  # type: ignore[arg-type]
    with pytest.raises(RepositoryUnavailableError):
        repository.save(_session("session-01"))


def test_mongo_playback_find_failure_raises_unavailable(
    failing_database: FailingDatabase,
) -> None:
    repository = MongoPlaybackSessionRepository(failing_database)  # type: ignore[arg-type]
    with pytest.raises(RepositoryUnavailableError):
        repository.find_by_id("session-01")


def test_mongo_playback_delete_failure_raises_unavailable(
    failing_database: FailingDatabase,
) -> None:
    repository = MongoPlaybackSessionRepository(failing_database)  # type: ignore[arg-type]
    with pytest.raises(RepositoryUnavailableError):
        repository.delete_by_id("session-01")
