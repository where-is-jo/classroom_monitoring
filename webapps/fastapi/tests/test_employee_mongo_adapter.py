"""직원 MongoDB adapter의 문서·index 계약 테스트."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.employees.adapters.mongo_repository import MongoEmployeeRepository
from app.employees.models import (
    Employee,
    EmployeeCurrentStatus,
    EmployeeObservation,
    EmployeeOverride,
    EmployeeStatus,
    EmployeeStatusHistory,
    StatusSource,
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


def _employee(*, user_id: str | None = "staff-id") -> Employee:
    now = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)
    return Employee(
        id="employee-id",
        employee_no="EMP-001",
        user_id=user_id,
        display_name="가상 직원",
        department="플랫폼팀",
        position="연구원",
        office_zone="A-101",
        is_active=True,
        current_status=EmployeeCurrentStatus(
            status=EmployeeStatus.OFFSITE,
            source=StatusSource.MANUAL,
            reason="외부 일정",
            effective_at=now,
            last_person_seen_at=now - timedelta(minutes=1),
        ),
        active_override=EmployeeOverride(
            status=EmployeeStatus.OFFSITE,
            reason="외부 일정",
            actor_user_id="staff-id",
            starts_at=now,
            ends_at=now + timedelta(hours=1),
        ),
        created_at=now,
        updated_at=now,
        version=2,
        created_operation_id="create-op",
        last_operation_id="override-op",
        operation_ids=("create-op", "profile-op", "override-op"),
    )


def test_직원_Mongo_index는_unique_sparse_CAS_조회에_필요한_구성을_가진다() -> None:
    database = RecordingDatabase()

    MongoEmployeeRepository.ensure_indexes(database)  # type: ignore[arg-type]

    employees = database.collections["employees"].indexes
    history = database.collections["employee_status_history"].indexes
    observations = database.collections["employee_observations"].indexes
    assert any(
        fields == [("employee_no", 1)] and options.get("unique") for fields, options in employees
    )
    assert any(
        fields == [("user_id", 1)] and options.get("unique") and options.get("sparse")
        for fields, options in employees
    )
    assert any(
        fields == [("operation_ids", 1)] and options.get("unique") for fields, options in employees
    )
    assert any(
        fields == [("operation_id", 1)] and options.get("unique") for fields, options in history
    )
    assert any(
        fields == [("event_id", 1)] and options.get("unique") for fields, options in observations
    )
    assert any(fields[0] == ("employee_id", 1) for fields, _ in history)


def test_직원_document_roundtrip은_중첩_상태_UTC_operation_ids를_보존한다() -> None:
    employee = _employee()

    document = MongoEmployeeRepository._employee_to_document(employee)
    restored = MongoEmployeeRepository._employee_to_domain(document)

    assert restored == employee
    assert document["operation_ids"] == ["create-op", "profile-op", "override-op"]
    assert restored.current_status.effective_at.tzinfo is not None


def test_연결없는_직원_document는_sparse_unique를_위해_user_id를_생략한다() -> None:
    document = MongoEmployeeRepository._employee_to_document(_employee(user_id=None))

    assert "user_id" not in document


def test_이력과_mock_관측_roundtrip은_source와_구조화_값만_저장한다() -> None:
    now = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)
    history = EmployeeStatusHistory(
        id="history-id",
        employee_id="employee-id",
        from_status=EmployeeStatus.AWAY,
        to_status=EmployeeStatus.WORKING,
        source=StatusSource.MOCK,
        reason="mock 관측: 사람 있음, 통화 없음",
        actor_user_id="admin-id",
        operation_id="event-id",
        occurred_at=now,
    )
    observation = EmployeeObservation(
        event_id="event-id",
        employee_id="employee-id",
        person_present=True,
        phone_detected=False,
        confidence=0.91,
        observed_at=now,
        received_at=now,
        resulting_status=EmployeeStatus.WORKING,
        status_changed=True,
    )

    history_document = MongoEmployeeRepository._history_to_document(history)
    observation_document = MongoEmployeeRepository._observation_to_document(observation)

    assert MongoEmployeeRepository._history_to_domain(history_document) == history
    assert MongoEmployeeRepository._observation_to_domain(observation_document) == observation
    assert observation_document["source"] == "MOCK"
    assert not ({"camera", "image", "video", "model", "provider"} & observation_document.keys())


def test_잘못된_Mongo_직원_문서는_내부값_없이_도메인_오류가_된다() -> None:
    document = MongoEmployeeRepository._employee_to_document(_employee())
    document["operation_ids"] = "not-a-list"

    with pytest.raises(RepositoryDataError):
        MongoEmployeeRepository._employee_to_domain(document)
