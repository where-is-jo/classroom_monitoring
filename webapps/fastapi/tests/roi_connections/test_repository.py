"""ROI repository의 카메라 범위와 legacy Mongo 문서 호환 테스트."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import cast

from app.roi_connections.adapters.mongo import MongoRoiConnectionRepository
from app.roi_connections.models import Point, RoiConnection
from app.shared.database import MongoDatabase

NOW = datetime(2026, 8, 15, 1, 0, tzinfo=UTC)


class FakeCollection:
    def __init__(self) -> None:
        self.documents: list[dict[str, object]] = []
        self.indexes: dict[str, dict[str, object]] = {
            "uq_roi_connections_classroom_seat": {},
            "uq_roi_connections_classroom_student": {},
        }
        self.dropped: list[str] = []

    def index_information(self) -> dict[str, dict[str, object]]:
        return dict(self.indexes)

    def drop_index(self, name: str) -> None:
        self.dropped.append(name)
        self.indexes.pop(name, None)

    def create_index(
        self,
        fields: list[tuple[str, int]],
        *,
        name: str,
        **options: object,
    ) -> None:
        self.indexes[name] = {"fields": fields, **options}

    def find(self, query: dict[str, object]) -> Iterable[dict[str, object]]:
        return [
            document
            for document in self.documents
            if all(document.get(key) == value for key, value in query.items())
        ]

    def find_one(self, query: dict[str, object]) -> dict[str, object] | None:
        return next(iter(self.find(query)), None)

    def find_one_and_update(
        self,
        query: dict[str, object],
        update: dict[str, dict[str, object]],
        *,
        upsert: bool,
        return_document: object,
    ) -> dict[str, object] | None:
        del upsert, return_document
        existing = self.find_one(query)
        document = dict(existing or query)
        document.update(update["$set"])
        if existing is not None:
            self.documents.remove(existing)
        self.documents.append(document)
        return document

    def delete_one(self, query: dict[str, object]) -> FakeDeleteResult:
        existing = self.find_one(query)
        if existing is None:
            return FakeDeleteResult(0)
        self.documents.remove(existing)
        return FakeDeleteResult(1)


class FakeDeleteResult:
    def __init__(self, deleted_count: int) -> None:
        self.deleted_count = deleted_count


class FakeDatabase:
    def __init__(self) -> None:
        self.collection = FakeCollection()

    def __getitem__(self, name: str) -> FakeCollection:
        assert name == "roi_connections"
        return self.collection


def _repository(database: FakeDatabase) -> MongoRoiConnectionRepository:
    return MongoRoiConnectionRepository(cast(MongoDatabase, database))


def _connection(camera_id: str, seat_id: str = "seat-a") -> RoiConnection:
    return RoiConnection(
        classroom_id="room",
        camera_id=camera_id,
        seat_id=seat_id,
        student_id="student-a",
        polygon=(Point(0.1, 0.1), Point(0.8, 0.1), Point(0.4, 0.8)),
        reference_image_revision=0,
        updated_at=NOW,
    )


def test_ensure_indexes_replaces_classroom_only_unique_indexes() -> None:
    database = FakeDatabase()

    MongoRoiConnectionRepository.ensure_indexes(cast(MongoDatabase, database))

    assert database.collection.dropped == [
        "uq_roi_connections_classroom_seat",
        "uq_roi_connections_classroom_student",
    ]
    assert "uq_roi_connections_classroom_camera_seat" in database.collection.indexes
    assert "uq_roi_connections_classroom_camera_student" in database.collection.indexes
    seat_index = database.collection.indexes["uq_roi_connections_classroom_camera_seat"]
    assert seat_index["unique"] is True
    assert seat_index["partialFilterExpression"] == {"camera_id": {"$type": "string"}}


def test_save_and_list_are_scoped_by_camera() -> None:
    database = FakeDatabase()
    repository = _repository(database)
    first = repository.save(_connection("camera-a"))
    second = repository.save(_connection("camera-b"))

    assert repository.list_by_camera("room", "camera-a") == [first]
    assert repository.list_by_camera("room", "camera-b") == [second]
    assert len(repository.list_by_classroom("room")) == 2
    assert repository.find_by_student("room", "camera-a", "student-a") == first


def test_legacy_document_without_camera_id_remains_readable() -> None:
    database = FakeDatabase()
    database.collection.documents.append(
        {
            "classroom_id": "room",
            "seat_id": "seat-a",
            "student_id": "student-a",
            "polygon": [
                {"x": 0.1, "y": 0.1},
                {"x": 0.8, "y": 0.1},
                {"x": 0.4, "y": 0.8},
            ],
            "reference_image_revision": 0,
            "updated_at": NOW,
        }
    )

    restored = _repository(database).list_by_classroom("room")

    assert len(restored) == 1
    assert restored[0].camera_id is None
    assert _repository(database).list_by_camera("room", "camera-a") == []


def test_delete_removes_only_the_matching_camera_scope() -> None:
    """같은 좌석이라도 카메라가 다르면 다른 ROI다. 하나를 지워도 나머지는 남는다."""
    database = FakeDatabase()
    repository = _repository(database)
    repository.save(_connection("camera-a"))
    repository.save(_connection("camera-b"))

    deleted = repository.delete("room", "camera-a", "seat-a")

    assert deleted is True
    assert [document["camera_id"] for document in database.collection.documents] == ["camera-b"]


def test_delete_reports_when_there_was_nothing_to_remove() -> None:
    repository = _repository(FakeDatabase())

    assert repository.delete("room", "camera-a", "seat-a") is False


def test_auto_generated_flag_survives_a_save_and_read() -> None:
    """확정 전이라는 사실이 저장에서 사라지면, 재시작 뒤 계산값이 판정에 들어간다."""
    database = FakeDatabase()
    repository = _repository(database)

    saved = repository.save(
        RoiConnection(
            classroom_id="room",
            camera_id="camera-a",
            seat_id="seat-a",
            student_id=None,
            polygon=(Point(0.1, 0.1), Point(0.8, 0.1), Point(0.4, 0.8)),
            reference_image_revision=3,
            updated_at=NOW,
            auto_generated=True,
        )
    )

    assert saved.auto_generated is True
    assert database.collection.documents[0]["auto_generated"] is True
    assert repository.list_by_camera("room", "camera-a")[0].auto_generated is True


def test_document_saved_before_the_flag_existed_counts_as_manual() -> None:
    """예전 문서는 사람이 그린 ROI다. 재검토 대상으로 바꾸면 멀쩡한 ROI가 판정에서 빠진다."""
    database = FakeDatabase()
    database.collection.documents.append(
        {
            "classroom_id": "room",
            "camera_id": "camera-a",
            "seat_id": "seat-a",
            "student_id": "student-a",
            "polygon": [{"x": 0.1, "y": 0.1}, {"x": 0.8, "y": 0.1}, {"x": 0.4, "y": 0.8}],
            "reference_image_revision": 0,
            "updated_at": NOW,
        }
    )

    restored = _repository(database).list_by_camera("room", "camera-a")

    assert restored[0].auto_generated is False
