"""면담 대기 MongoDB index와 문서 변환 계약 테스트."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.interview_waits.adapters.mongo_repository import MongoInterviewWaitRepository
from app.interview_waits.models import (
    InterviewWait,
    InterviewWaitHistory,
    InterviewWaitStatus,
)
from app.shared.errors import RepositoryDataError


class RecordingCollection:
    def __init__(self) -> None:
        self.indexes: list[tuple[list[tuple[str, int]], dict[str, object]]] = []

    def create_index(self, fields: list[tuple[str, int]], **options: object) -> None:
        self.indexes.append((fields, options))


class RecordingDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, RecordingCollection] = {}

    def __getitem__(self, name: str) -> RecordingCollection:
        return self.collections.setdefault(name, RecordingCollection())


def _wait() -> InterviewWait:
    now = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)
    return InterviewWait(
        id="wait-id",
        requester_user_id="student-id",
        employee_id="employee-id",
        status=InterviewWaitStatus.READY,
        message="면담 요청",
        requested_at=now,
        ready_at=now + timedelta(minutes=1),
        completed_at=None,
        cancelled_at=None,
        expires_at=now + timedelta(hours=24),
        version=1,
        active_key="student-id:employee-id",
        created_operation_id="create-op",
        last_operation_id="ready-op",
        operation_ids=("create-op", "ready-op"),
        last_actor_user_id="admin-id",
    )


def test_면담대기_index는_active_key_operation_CAS_조회와_history를_지원한다() -> None:
    database = RecordingDatabase()

    MongoInterviewWaitRepository.ensure_indexes(database)  # type: ignore[arg-type]

    waits = database.collections["interview_waits"].indexes
    history = database.collections["interview_wait_history"].indexes
    assert any(
        fields == [("active_key", 1)] and options.get("unique") and options.get("sparse")
        for fields, options in waits
    )
    assert any(
        fields == [("operation_ids", 1)] and options.get("unique") for fields, options in waits
    )
    assert any(fields == [("expires_at", 1), ("status", 1)] for fields, _ in waits)
    assert any(
        fields == [("operation_id", 1)] and options.get("unique") for fields, options in history
    )


def test_면담대기와_history_document_roundtrip은_UTC와_active_key를_보존한다() -> None:
    wait = _wait()
    history = InterviewWaitHistory(
        id="history-id",
        wait_id=wait.id,
        from_status=InterviewWaitStatus.WAITING,
        to_status=InterviewWaitStatus.READY,
        reason="직원 복귀",
        actor_user_id="admin-id",
        operation_id="ready-op",
        occurred_at=wait.ready_at,  # type: ignore[arg-type]
    )

    wait_document = MongoInterviewWaitRepository._wait_to_document(wait)
    history_document = MongoInterviewWaitRepository._history_to_document(history)

    assert MongoInterviewWaitRepository._wait_to_domain(wait_document) == wait
    assert MongoInterviewWaitRepository._history_to_domain(history_document) == history
    assert wait_document["operation_ids"] == ["create-op", "ready-op"]


def test_종료대기는_sparse_unique를_위해_active_key를_생략한다() -> None:
    wait = _wait()
    terminal = replace(wait, status=InterviewWaitStatus.CANCELLED, active_key=None)

    document = MongoInterviewWaitRepository._wait_to_document(terminal)

    assert "active_key" not in document


def test_잘못된_operation_ids와_naive_datetime은_저장소오류가_된다() -> None:
    document = MongoInterviewWaitRepository._wait_to_document(_wait())
    document["operation_ids"] = "not-a-list"
    with pytest.raises(RepositoryDataError):
        MongoInterviewWaitRepository._wait_to_domain(document)

    document = MongoInterviewWaitRepository._wait_to_document(_wait())
    document["requested_at"] = datetime(2026, 8, 5, 9, 0)  # noqa: DTZ001
    with pytest.raises(RepositoryDataError):
        MongoInterviewWaitRepository._wait_to_domain(document)
