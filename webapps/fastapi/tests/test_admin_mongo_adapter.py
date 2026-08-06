"""MongoDB dashboard index and bounded-query contracts."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import cast

from app.admin.adapters.mongo_repository import MongoAdminDashboardRepository
from app.admin.models import DashboardActivityType


class RecordingCollection:
    def __init__(self, documents: list[dict[str, object]] | None = None) -> None:
        self.indexes: list[tuple[list[tuple[str, int]], dict[str, object]]] = []
        self.documents = list(documents or [])
        self.limits: list[int] = []

    def create_index(self, fields: list[tuple[str, int]], **options: object) -> None:
        self.indexes.append((fields, options))

    def find(self, *_args: object, **_kwargs: object) -> RecordingCursor:
        return RecordingCursor(self)

    def count_documents(self, _query: object) -> int:
        return len(self.documents)


class RecordingCursor:
    def __init__(self, collection: RecordingCollection) -> None:
        self.collection = collection
        self.documents = list(collection.documents)

    def sort(self, fields: list[tuple[str, int]]) -> RecordingCursor:
        self.documents.sort(
            key=lambda item: (
                -cast(datetime, item[fields[0][0]]).timestamp(),
                str(item["_id"]),
            )
        )
        return self

    def limit(self, value: int) -> RecordingCursor:
        self.collection.limits.append(value)
        self.documents = self.documents[:value]
        return self

    def __iter__(self) -> Iterator[dict[str, object]]:
        return iter(self.documents)


class RecordingDatabase:
    def __init__(self, documents: list[dict[str, object]] | None = None) -> None:
        self.collections: dict[str, RecordingCollection] = {}
        if documents is not None:
            self.collections["notifications"] = RecordingCollection(documents)

    def __getitem__(self, name: str) -> RecordingCollection:
        return self.collections.setdefault(name, RecordingCollection())


def test_dashboard_indexes_cover_recent_sort_filters_and_audit_action() -> None:
    database = RecordingDatabase()
    MongoAdminDashboardRepository.ensure_indexes(database)  # type: ignore[arg-type]

    assert any(
        fields == [("occurred_at", -1), ("_id", 1)]
        for fields, _ in database.collections["employee_status_history"].indexes
    )
    assert any(
        fields == [("status", 1), ("attempted_at", -1)]
        for fields, _ in database.collections["notification_deliveries"].indexes
    )
    assert any(
        fields == [("action", 1), ("occurred_at", -1)]
        for fields, _ in database.collections["audit_logs"].indexes
    )


def test_recent_source_query_is_bounded_before_cross_source_merge() -> None:
    now = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)
    documents = [
        {
            "_id": f"notification-{index:02d}",
            "title": f"알림 {index}",
            "type": "TEST",
            "created_at": now - timedelta(minutes=index),
        }
        for index in range(20)
    ]
    database = RecordingDatabase(documents)
    repository = MongoAdminDashboardRepository(database)  # type: ignore[arg-type]

    page = repository.list_activities(
        activity_type=DashboardActivityType.NOTIFICATION,
        from_time=now - timedelta(days=1),
        to_time=now + timedelta(seconds=1),
        limit=5,
        offset=10,
    )

    assert database.collections["notifications"].limits == [15]
    assert len(page.items) == 5
    assert page.total == 20
