"""좌석-학생 지정 UoW 통합 테스트.

- memory: 단일 공유 store·lock 기반 원자 연산과 race 결과
- mongo: transaction topology 검사, transient retry, commit uncertainty
- service: 주입된 UoW만 mutation하고 에러 매핑(404/422)을 유지
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from threading import Barrier, Thread

import pytest
from pymongo.errors import AutoReconnect, OperationFailure

from app.classrooms.adapters.memory_repository import (
    InMemoryClassroomRepository,
    InMemorySeatAssignmentRepository,
    InMemorySeatMutationUnitOfWork,
)
from app.classrooms.adapters.mongo_repository import (
    MongoClassroomRepository,
    MongoSeatAssignmentRepository,
    MongoSeatMutationUnitOfWork,
    _is_ambiguous_commit_error,
    _is_transient_transaction_error,
)
from app.classrooms.errors import SeatInactiveForAssignmentError, SeatNotFoundError
from app.classrooms.models import (
    Classroom,
    OccupancySource,
    Seat,
    SeatAssignment,
    SeatCurrentOccupancy,
    SeatOccupancy,
)
from app.classrooms.service import ClassroomService
from app.shared.adapters.memory_student_lookup import InMemoryStudentLookup
from app.shared.database import DatabaseOperationError
from app.shared.errors import RepositoryUnavailableError, StudentNotFoundError
from app.shared.student_identity import StudentIdentity

_FIXED_NOW = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)


# ============================================================
# memory fixture
# ============================================================


def _fixed_now() -> datetime:
    return _FIXED_NOW


def _build_memory_store() -> InMemoryClassroomRepository:
    now = _FIXED_NOW
    store = InMemoryClassroomRepository()
    store.create_classroom(
        Classroom(
            id="cls-001",
            code="R101",
            name="강의실1",
            location="본관",
            is_active=True,
            created_at=now,
        )
    )
    for seat_id, code in (("seat-001", "S01"), ("seat-002", "S02")):
        store.create_seat(
            Seat(
                id=seat_id,
                classroom_id="cls-001",
                code=code,
                label=f"좌석 {code}",
                is_active=True,
                current_occupancy=SeatCurrentOccupancy(
                    state=SeatOccupancy.UNKNOWN,
                    source=OccupancySource.SYSTEM,
                    confidence=None,
                    observed_at=None,
                    event_id=None,
                ),
                created_at=now,
                updated_at=now,
                version=0,
            )
        )
    return store


def _build_memory_service() -> tuple[
    InMemoryClassroomRepository, InMemorySeatMutationUnitOfWork, ClassroomService
]:
    store = _build_memory_store()
    uow = InMemorySeatMutationUnitOfWork(store, clock=_fixed_now)
    student_lookup = InMemoryStudentLookup(
        identities=(
            StudentIdentity(id="stu-001", student_no="20260101", name="홍길동", is_active=True),
            StudentIdentity(id="stu-002", student_no="20269999", name="김비활성", is_active=False),
            StudentIdentity(id="stu-003", student_no="20260203", name="이지원", is_active=True),
        )
    )
    assignment_repo = InMemorySeatAssignmentRepository(store=store)
    service = ClassroomService(
        store,
        student_lookup=student_lookup,
        assignment_repository=assignment_repo,
        uow=uow,
        occupancy_confidence_threshold=0.6,
        clock=_fixed_now,
    )
    return store, uow, service


def _deactivate_seat(store: InMemoryClassroomRepository, seat_id: str) -> None:
    seat = store.get_seat(seat_id)
    assert seat is not None
    store.update_seat(replace(seat, is_active=False))


class RecordingSeatAssignmentRepository(InMemorySeatAssignmentRepository):
    """mutation 호출을 기록하는 assignment 저장소 (직접 mutation 여부 검증용)."""

    def __init__(self, store: InMemoryClassroomRepository) -> None:
        super().__init__(store=store)
        self.mutations: list[tuple[str, str]] = []

    def assign(self, assignment: SeatAssignment) -> SeatAssignment:
        self.mutations.append(("assign", assignment.seat_id))
        return super().assign(assignment)

    def unassign(self, seat_id: str) -> None:
        self.mutations.append(("unassign", seat_id))
        super().unassign(seat_id)

    def unassign_by_student(self, student_id: str) -> int:
        self.mutations.append(("unassign_by_student", student_id))
        return super().unassign_by_student(student_id)


# ============================================================
# memory 원자 연산
# ============================================================


class TestInMemoryUowAtomic:
    def test_assign_or_move_if_active_moves_student(self) -> None:
        store, uow, _ = _build_memory_service()
        first = uow.assign_or_move_if_active("seat-001", "stu-001", "cls-001")
        assert first.seat_id == "seat-001"

        moved = uow.assign_or_move_if_active("seat-002", "stu-001", "cls-001")
        assert moved.seat_id == "seat-002"
        assert store.get_assignment_by_seat("seat-001") is None
        current = store.get_assignment_by_student("stu-001", "cls-001")
        assert current is not None
        assert current.seat_id == "seat-002"
        assert len(store.list_assignments_by_classroom("cls-001")) == 1

    def test_assign_idempotent_same_seat(self) -> None:
        store, uow, _ = _build_memory_service()
        uow.assign_or_move_if_active("seat-001", "stu-001", "cls-001")
        uow.assign_or_move_if_active("seat-001", "stu-001", "cls-001")
        assert len(store.list_assignments_by_classroom("cls-001")) == 1

    def test_assign_replaces_student_on_seat(self) -> None:
        store, uow, _ = _build_memory_service()
        uow.assign_or_move_if_active("seat-001", "stu-001", "cls-001")
        uow.assign_or_move_if_active("seat-001", "stu-003", "cls-001")
        current = store.get_assignment_by_seat("seat-001")
        assert current is not None
        assert current.student_id == "stu-003"
        assert len(store.list_assignments_by_classroom("cls-001")) == 1

    def test_assign_missing_seat_raises(self) -> None:
        _, uow, _ = _build_memory_service()
        with pytest.raises(SeatNotFoundError):
            uow.assign_or_move_if_active("seat-999", "stu-001", "cls-001")

    def test_assign_inactive_seat_raises(self) -> None:
        store, uow, _ = _build_memory_service()
        _deactivate_seat(store, "seat-002")
        with pytest.raises(SeatInactiveForAssignmentError):
            uow.assign_or_move_if_active("seat-002", "stu-001", "cls-001")

    def test_delete_seat_and_unassign_removes_seat_and_assignment(self) -> None:
        """hard delete: 좌석 레코드와 현재 지정이 함께 물리적으로 사라진다."""
        store, uow, _ = _build_memory_service()
        uow.assign_or_move_if_active("seat-001", "stu-001", "cls-001")
        uow.delete_seat_and_unassign("seat-001")
        assert store.get_assignment_by_seat("seat-001") is None
        assert store.get_seat("seat-001") is None
        assert store.list_assignments_by_classroom("cls-001") == []

    def test_delete_seat_twice_raises_not_found(self) -> None:
        """반복 삭제는 404 SEAT_NOT_FOUND다 (레코드가 남지 않는다)."""
        _, uow, _ = _build_memory_service()
        uow.delete_seat_and_unassign("seat-001")
        with pytest.raises(SeatNotFoundError):
            uow.delete_seat_and_unassign("seat-001")

    def test_unassign_if_active_removes_assignment(self) -> None:
        store, uow, _ = _build_memory_service()
        uow.assign_or_move_if_active("seat-001", "stu-001", "cls-001")
        uow.unassign_if_active("seat-001")
        assert store.get_assignment_by_seat("seat-001") is None

    def test_unassign_if_active_ignores_inactive_seat(self) -> None:
        store, uow, _ = _build_memory_service()
        uow.assign_or_move_if_active("seat-002", "stu-001", "cls-001")
        _deactivate_seat(store, "seat-002")
        uow.unassign_if_active("seat-002")
        assert store.get_assignment_by_seat("seat-002") is not None

    def test_unassign_if_active_ignores_missing_seat(self) -> None:
        _, uow, _ = _build_memory_service()
        uow.unassign_if_active("seat-999")  # 예외 없이 무시


# ============================================================
# memory 동시성 (단일 공유 lock)
# ============================================================


class TestInMemoryUowConcurrency:
    def test_concurrent_assign_two_students_same_seat_single_winner(self) -> None:
        store, uow, _ = _build_memory_service()
        barrier = Barrier(2)
        outcomes: list[Exception | None] = []

        def assign(student_id: str) -> None:
            barrier.wait()
            try:
                uow.assign_or_move_if_active("seat-001", student_id, "cls-001")
                outcomes.append(None)
            except Exception as exc:
                outcomes.append(exc)

        threads = [
            Thread(target=assign, args=("stu-001",)),
            Thread(target=assign, args=("stu-003",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        winner = store.get_assignment_by_seat("seat-001")
        assert winner is not None
        assert winner.student_id in {"stu-001", "stu-003"}
        assert len(store.list_assignments_by_classroom("cls-001")) == 1

    def test_concurrent_same_student_two_seats_single_assignment(self) -> None:
        store, uow, _ = _build_memory_service()
        barrier = Barrier(2)
        outcomes: list[Exception | None] = []

        def assign(seat_id: str) -> None:
            barrier.wait()
            try:
                uow.assign_or_move_if_active(seat_id, "stu-001", "cls-001")
                outcomes.append(None)
            except Exception as exc:
                outcomes.append(exc)

        threads = [
            Thread(target=assign, args=("seat-001",)),
            Thread(target=assign, args=("seat-002",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assignments = store.list_assignments_by_classroom("cls-001")
        assert len(assignments) == 1
        assert assignments[0].student_id == "stu-001"
        assert assignments[0].seat_id in {"seat-001", "seat-002"}

    def test_concurrent_delete_and_assign_consistent_state(self) -> None:
        """delete/assign 경쟁: 어느 순서든 좌석·지정이 함께 사라진다.

        delete-first면 assign이 404 SEAT_NOT_FOUND로 지고, assign-first여도
        delete가 같은 lock 안에서 지정까지 지우므로 고아 지정이 남지 않는다.
        """
        store, uow, _ = _build_memory_service()
        uow.assign_or_move_if_active("seat-001", "stu-001", "cls-001")
        barrier = Barrier(2)
        outcomes: dict[str, Exception | None] = {}

        def do_delete() -> None:
            barrier.wait()
            try:
                uow.delete_seat_and_unassign("seat-001")
                outcomes["delete"] = None
            except Exception as exc:
                outcomes["delete"] = exc

        def do_assign() -> None:
            barrier.wait()
            try:
                uow.assign_or_move_if_active("seat-001", "stu-003", "cls-001")
                outcomes["assign"] = None
            except Exception as exc:
                outcomes["assign"] = exc

        threads = [Thread(target=do_delete), Thread(target=do_assign)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        # winner 상태 일관성: 좌석도 지정도 남지 않는다.
        assert store.get_seat("seat-001") is None
        assert store.get_assignment_by_seat("seat-001") is None
        assert store.list_assignments_by_classroom("cls-001") == []
        assert outcomes["delete"] is None
        assert outcomes["assign"] is None or isinstance(outcomes["assign"], SeatNotFoundError)


# ============================================================
# service: UoW 주입 시 직접 mutation 금지 + 에러 매핑
# ============================================================


class TestServiceUsesInjectedUow:
    def test_assign_mutates_shared_store_through_uow(self) -> None:
        store, _, service = _build_memory_service()
        info = service.assign_student("seat-001", "stu-001")
        assert info.seat_id == "seat-001"
        assignments = service.list_assignments("cls-001")
        assert len(assignments) == 1
        assert assignments[0].student_id == "stu-001"
        # UoW가 쓴 shared store가 조회에도 그대로 보인다.
        assert len(store.list_assignments_by_classroom("cls-001")) == 1

    def test_service_never_mutates_assignment_repository_when_uow_injected(self) -> None:
        store = _build_memory_store()
        uow = InMemorySeatMutationUnitOfWork(store, clock=_fixed_now)
        spy = RecordingSeatAssignmentRepository(store=store)
        student_lookup = InMemoryStudentLookup(
            identities=(
                StudentIdentity(id="stu-001", student_no="20260101", name="홍길동", is_active=True),
                StudentIdentity(id="stu-003", student_no="20260203", name="이지원", is_active=True),
            )
        )
        service = ClassroomService(
            store,
            student_lookup=student_lookup,
            assignment_repository=spy,
            uow=uow,
            occupancy_confidence_threshold=0.6,
            clock=_fixed_now,
        )

        service.assign_student("seat-001", "stu-001")
        service.assign_student("seat-002", "stu-001")  # 이동
        service.unassign_student("seat-002")
        service.assign_student("seat-001", "stu-003")
        service.delete_seat("seat-001")

        assert spy.mutations == []

    def test_delete_seat_removes_seat_and_unassigns_through_uow(self) -> None:
        store, _, service = _build_memory_service()
        service.assign_student("seat-001", "stu-001")
        service.delete_seat("seat-001")
        assert store.get_seat("seat-001") is None
        assert store.get_assignment_by_seat("seat-001") is None
        assert service.list_assignments("cls-001") == []
        # 후속 mutation은 404 SEAT_NOT_FOUND다.
        with pytest.raises(SeatNotFoundError):
            service.delete_seat("seat-001")
        with pytest.raises(SeatNotFoundError):
            service.assign_student("seat-001", "stu-001")
        with pytest.raises(SeatNotFoundError):
            service.update_seat("seat-001", label="이름 변경")


class TestServiceErrorMapping:
    def test_assign_missing_seat_404(self) -> None:
        _, _, service = _build_memory_service()
        with pytest.raises(SeatNotFoundError) as exc_info:
            service.assign_student("seat-999", "stu-001")
        assert exc_info.value.status_code == 404
        assert exc_info.value.code == "SEAT_NOT_FOUND"

    def test_assign_inactive_seat_422(self) -> None:
        store, _, service = _build_memory_service()
        _deactivate_seat(store, "seat-002")
        with pytest.raises(SeatInactiveForAssignmentError) as exc_info:
            service.assign_student("seat-002", "stu-001")
        assert exc_info.value.status_code == 422
        assert exc_info.value.code == "SEAT_INACTIVE_FOR_ASSIGNMENT"

    def test_assign_missing_student_404(self) -> None:
        _, _, service = _build_memory_service()
        with pytest.raises(StudentNotFoundError) as exc_info:
            service.assign_student("seat-001", "stu-404")
        assert exc_info.value.status_code == 404
        assert exc_info.value.code == "STUDENT_NOT_FOUND"


# ============================================================
# mongo fake
# ============================================================


def _ambiguous_commit_error() -> OperationFailure:
    return OperationFailure(
        "commit outcome unknown",
        details={"errorLabels": ["UnknownTransactionCommitResult"]},
    )


def _ambiguous_autoreconnect_error() -> AutoReconnect:
    """commit 시점 AutoReconnect: PyMongo가 UnknownTransactionCommitResult label을 붙인다."""
    error = AutoReconnect("commit outcome unknown")
    error._add_error_label("UnknownTransactionCommitResult")
    return error


def _transient_error() -> AutoReconnect:
    return AutoReconnect("primary not available")


class FakeTransactionContext:
    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None


class FakeCommitState:
    def __init__(self, behaviors: Sequence[BaseException | None]) -> None:
        self.behaviors = list(behaviors)
        self.attempts = 0
        self.commits = 0


class FakeSession:
    def __init__(self, state: FakeCommitState) -> None:
        self._state = state

    def start_transaction(self) -> FakeTransactionContext:
        return FakeTransactionContext()

    def commit_transaction(self) -> None:
        self._state.commits += 1
        if self._state.behaviors:
            behavior = self._state.behaviors.pop(0)
            if behavior is not None:
                raise behavior

    def end_session(self) -> None:
        self._state.attempts += 1


class FakeClient:
    def __init__(self, state: FakeCommitState) -> None:
        self._state = state

    def start_session(self) -> FakeSession:
        return FakeSession(self._state)


class FakeWriteResult:
    def __init__(self, matched_count: int) -> None:
        self.matched_count = matched_count
        self.modified_count = matched_count
        self.deleted_count = matched_count


class FakeCollection:
    """find/delete/replace/update를 지원하는 인메모리 컬렉션 fake.

    ``persist_writes=False``면 쓰기가 저장소에 반영되지 않는다
    (transaction rollback 시뮬레이션).
    """

    def __init__(
        self,
        documents: list[dict[str, object]] | None = None,
        *,
        persist_writes: bool = True,
    ) -> None:
        self.documents = list(documents or [])
        self.persist_writes = persist_writes
        self.replace_calls: list[tuple[dict[str, object], dict[str, object]]] = []
        self.delete_calls: list[dict[str, object]] = []
        self.insert_calls: list[dict[str, object]] = []
        self.update_calls: list[tuple[dict[str, object], dict[str, object]]] = []

    def find_one(
        self, query: dict[str, object], session: object = None
    ) -> dict[str, object] | None:
        del session
        return next(
            (
                document
                for document in self.documents
                if all(document.get(key) == value for key, value in query.items())
            ),
            None,
        )

    def find(self, query: dict[str, object], session: object = None) -> list[dict[str, object]]:
        del session
        return [
            document
            for document in self.documents
            if all(document.get(key) == value for key, value in query.items())
        ]

    def replace_one(
        self,
        query: dict[str, object],
        replacement: dict[str, object],
        upsert: bool = False,
        session: object = None,
    ) -> FakeWriteResult:
        del session
        self.replace_calls.append((query, replacement))
        index = next(
            (
                i
                for i, document in enumerate(self.documents)
                if all(document.get(key) == value for key, value in query.items())
            ),
            None,
        )
        if index is None:
            if upsert and self.persist_writes:
                self.documents.append(replacement)
            return FakeWriteResult(0)
        if self.persist_writes:
            self.documents[index] = replacement
        return FakeWriteResult(1)

    def insert_one(self, document: dict[str, object], session: object = None) -> None:
        del session
        self.insert_calls.append(document)
        if self.persist_writes:
            self.documents.append(document)

    def delete_one(self, query: dict[str, object], session: object = None) -> FakeWriteResult:
        del session
        self.delete_calls.append(query)
        match = next(
            (
                document
                for document in self.documents
                if all(document.get(key) == value for key, value in query.items())
            ),
            None,
        )
        # transaction 안에서 본 삭제 대상 수를 보고하고, 반영 여부는
        # persist_writes로 나눈다 (commit 실패 시 rollback 시뮬레이션).
        if match is not None and self.persist_writes:
            self.documents = [document for document in self.documents if document is not match]
        return FakeWriteResult(1 if match is not None else 0)

    def delete_many(self, query: dict[str, object], session: object = None) -> FakeWriteResult:
        del session
        self.delete_calls.append(query)
        matches = [
            document
            for document in self.documents
            if all(document.get(key) == value for key, value in query.items())
        ]
        if self.persist_writes:
            matched_ids = {id(document) for document in matches}
            self.documents = [
                document for document in self.documents if id(document) not in matched_ids
            ]
        return FakeWriteResult(len(matches))

    def update_one(
        self,
        query: dict[str, object],
        update: dict[str, object],
        session: object = None,
    ) -> FakeWriteResult:
        del session
        self.update_calls.append((query, update))
        index = next(
            (
                i
                for i, document in enumerate(self.documents)
                if all(document.get(key) == value for key, value in query.items())
            ),
            None,
        )
        if index is None:
            return FakeWriteResult(0)
        if self.persist_writes:
            set_values = update.get("$set")
            if not isinstance(set_values, dict):
                raise TypeError
            merged = {**self.documents[index], **set_values}
            self.documents[index] = merged
        return FakeWriteResult(1)


class FakeDatabase:
    def __init__(
        self,
        hello_response: dict[str, object],
        state: FakeCommitState,
        *,
        collections: dict[str, FakeCollection] | None = None,
    ) -> None:
        self._hello = hello_response
        self._collections = collections or {}
        self._client = FakeClient(state)
        self.hello_calls = 0

    def __getitem__(self, name: str) -> FakeCollection:
        return self._collections.setdefault(name, FakeCollection())

    def __setitem__(self, name: str, collection: FakeCollection) -> None:
        self._collections[name] = collection

    def command(self, name: str) -> dict[str, object]:
        if name == "hello":
            self.hello_calls += 1
            return self._hello
        raise AssertionError(f"예상치 못한 command: {name}")

    @property
    def client(self) -> FakeClient:
        return self._client


def _seat_document(seat_id: str, *, is_active: bool = True) -> dict[str, object]:
    return {
        "_id": seat_id,
        "classroom_id": "cls-001",
        "code": f"S-{seat_id}",
        "is_active": is_active,
    }


def _assignment_document(
    seat_id: str, student_id: str, *, classroom_id: str = "cls-001"
) -> dict[str, object]:
    return {
        "_id": seat_id,
        "seat_id": seat_id,
        "student_id": student_id,
        "classroom_id": classroom_id,
        "assigned_at": _FIXED_NOW,
    }


def _build_mongo_uow(
    state: FakeCommitState,
    *,
    hello: dict[str, object] | None = None,
    seats: list[dict[str, object]] | None = None,
    assignments: list[dict[str, object]] | None = None,
    assignments_persist: bool = True,
    seats_persist: bool = True,
    history: list[dict[str, object]] | None = None,
    history_persist: bool = True,
) -> tuple[MongoSeatMutationUnitOfWork, FakeDatabase]:
    hello_response = hello or {"setName": "rs0", "isWritablePrimary": True}
    database = FakeDatabase(
        hello_response,
        state,
        collections={
            MongoSeatMutationUnitOfWork.assignment_collection_name: FakeCollection(
                assignments, persist_writes=assignments_persist
            )
        },
    )
    database[MongoClassroomRepository.seat_collection_name] = FakeCollection(
        seats or [], persist_writes=seats_persist
    )
    database[MongoClassroomRepository.history_collection_name] = FakeCollection(
        history or [], persist_writes=history_persist
    )
    repository = MongoClassroomRepository(database)  # type: ignore[arg-type]
    uow = MongoSeatMutationUnitOfWork(repository, database, clock=_fixed_now)  # type: ignore[arg-type]
    return uow, database


# ============================================================
# mongo topology 검사
# ============================================================


class TestMongoTopology:
    def test_standalone_hello_fails_startup(self) -> None:
        state = FakeCommitState([])
        database = FakeDatabase({"isWritablePrimary": True}, state)
        repository = MongoClassroomRepository(database)  # type: ignore[arg-type]
        with pytest.raises(DatabaseOperationError):
            MongoSeatMutationUnitOfWork(repository, database, clock=_fixed_now)  # type: ignore[arg-type]

    def test_replica_set_hello_passes(self) -> None:
        state = FakeCommitState([])
        database = FakeDatabase({"setName": "rs0", "isWritablePrimary": True}, state)
        repository = MongoClassroomRepository(database)  # type: ignore[arg-type]
        MongoSeatMutationUnitOfWork(repository, database, clock=_fixed_now)  # type: ignore[arg-type]
        assert database.hello_calls == 1

    def test_mongos_hello_passes(self) -> None:
        state = FakeCommitState([])
        database = FakeDatabase({"msg": "isdbgrid"}, state)
        repository = MongoClassroomRepository(database)  # type: ignore[arg-type]
        MongoSeatMutationUnitOfWork(repository, database, clock=_fixed_now)  # type: ignore[arg-type]

    def test_hello_error_fails_startup(self) -> None:
        state = FakeCommitState([])

        class FailingDatabase(FakeDatabase):
            def command(self, name: str) -> dict[str, object]:
                del name
                raise AutoReconnect("connection lost")

        database = FailingDatabase({"setName": "rs0"}, state)
        repository = MongoClassroomRepository(database)  # type: ignore[arg-type]
        with pytest.raises(DatabaseOperationError):
            MongoSeatMutationUnitOfWork(repository, database, clock=_fixed_now)  # type: ignore[arg-type]


# ============================================================
# mongo 인덱스
# ============================================================


class TestMongoIndexes:
    def test_assignment_collection_indexes_are_unique(self) -> None:
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

        database = RecordingDatabase()
        MongoSeatMutationUnitOfWork.ensure_indexes(database)  # type: ignore[arg-type]
        indexes = database.collections[
            MongoSeatMutationUnitOfWork.assignment_collection_name
        ].indexes
        assert any(
            fields == [("seat_id", 1)] and options.get("unique") for fields, options in indexes
        )
        assert any(
            fields == [("classroom_id", 1), ("student_id", 1)] and options.get("unique")
            for fields, options in indexes
        )


# ============================================================
# mongo 원자 연산
# ============================================================


class TestMongoUowOperations:
    def test_assign_or_move_if_active_writes_assignment(self) -> None:
        state = FakeCommitState([None])
        uow, database = _build_mongo_uow(state, seats=[_seat_document("seat-001")])
        result = uow.assign_or_move_if_active("seat-001", "stu-001", "cls-001")
        assert result.seat_id == "seat-001"
        assert result.student_id == "stu-001"
        assignments = database[MongoSeatMutationUnitOfWork.assignment_collection_name]
        document = assignments.find_one({"seat_id": "seat-001"})
        assert document is not None
        assert document["student_id"] == "stu-001"

    def test_assign_or_move_unassigns_previous_seat(self) -> None:
        state = FakeCommitState([None, None])
        uow, database = _build_mongo_uow(
            state,
            seats=[_seat_document("seat-001"), _seat_document("seat-002")],
        )
        uow.assign_or_move_if_active("seat-001", "stu-001", "cls-001")
        result = uow.assign_or_move_if_active("seat-002", "stu-001", "cls-001")
        assert result.seat_id == "seat-002"
        assignments = database[MongoSeatMutationUnitOfWork.assignment_collection_name]
        assert assignments.find_one({"seat_id": "seat-001"}) is None
        assert assignments.find_one({"seat_id": "seat-002"}) is not None

    def test_assign_missing_seat_raises(self) -> None:
        state = FakeCommitState([])
        uow, _ = _build_mongo_uow(state)
        with pytest.raises(SeatNotFoundError):
            uow.assign_or_move_if_active("seat-999", "stu-001", "cls-001")

    def test_assign_inactive_seat_raises(self) -> None:
        state = FakeCommitState([])
        uow, _ = _build_mongo_uow(state, seats=[_seat_document("seat-001", is_active=False)])
        with pytest.raises(SeatInactiveForAssignmentError):
            uow.assign_or_move_if_active("seat-001", "stu-001", "cls-001")

    def test_delete_seat_and_unassign(self) -> None:
        """같은 transaction에서 seat 문서와 지정을 모두 지운다 (hard delete)."""
        state = FakeCommitState([None])
        uow, database = _build_mongo_uow(
            state,
            seats=[_seat_document("seat-001"), _seat_document("seat-002")],
            assignments=[_assignment_document("seat-001", "stu-001")],
        )
        uow.delete_seat_and_unassign("seat-001")
        seats = database[MongoClassroomRepository.seat_collection_name]
        assignments = database[MongoSeatMutationUnitOfWork.assignment_collection_name]
        assert seats.find_one({"_id": "seat-001"}) is None
        assert assignments.find_one({"seat_id": "seat-001"}) is None
        # 다른 좌석은 건드리지 않는다.
        assert seats.find_one({"_id": "seat-002"}) is not None

    def test_delete_seat_twice_raises_not_found(self) -> None:
        """문서가 사라지므로 반복 삭제는 SeatNotFoundError다."""
        state = FakeCommitState([None, None])
        uow, _ = _build_mongo_uow(state, seats=[_seat_document("seat-001")])
        uow.delete_seat_and_unassign("seat-001")
        with pytest.raises(SeatNotFoundError):
            uow.delete_seat_and_unassign("seat-001")

    def test_delete_seat_keeps_history_and_batches(self) -> None:
        """history·batch 컬렉션은 cascade 삭제하지 않는다."""
        state = FakeCommitState([None])
        uow, database = _build_mongo_uow(state, seats=[_seat_document("seat-001")])
        history = database[MongoClassroomRepository.history_collection_name]
        batches = database[MongoClassroomRepository.batch_collection_name]
        history.documents.append({"_id": "hist-1", "seat_id": "seat-001", "event_id": "evt-1"})
        batches.documents.append({"_id": "evt-1", "classroom_id": "cls-001"})

        uow.delete_seat_and_unassign("seat-001")

        assert history.find_one({"seat_id": "seat-001"}) is not None
        assert batches.find_one({"_id": "evt-1"}) is not None
        assert history.delete_calls == []
        assert batches.delete_calls == []

    def test_delete_missing_seat_raises(self) -> None:
        state = FakeCommitState([])
        uow, _ = _build_mongo_uow(state)
        with pytest.raises(SeatNotFoundError):
            uow.delete_seat_and_unassign("seat-999")

    def test_unassign_if_active(self) -> None:
        state = FakeCommitState([None])
        uow, database = _build_mongo_uow(
            state,
            seats=[_seat_document("seat-001")],
            assignments=[_assignment_document("seat-001", "stu-001")],
        )
        uow.unassign_if_active("seat-001")
        assignments = database[MongoSeatMutationUnitOfWork.assignment_collection_name]
        assert assignments.find_one({"seat_id": "seat-001"}) is None

    def test_unassign_if_active_ignores_inactive_seat(self) -> None:
        state = FakeCommitState([None])
        uow, database = _build_mongo_uow(
            state,
            seats=[_seat_document("seat-001", is_active=False)],
            assignments=[_assignment_document("seat-001", "stu-001")],
        )
        uow.unassign_if_active("seat-001")
        assignments = database[MongoSeatMutationUnitOfWork.assignment_collection_name]
        assert assignments.find_one({"seat_id": "seat-001"}) is not None


# ============================================================
# mongo transient retry / commit uncertainty / unexpected 503
# ============================================================


class TestMongoRetryAndCommitUncertainty:
    def test_transient_error_retries_then_succeeds(self) -> None:
        state = FakeCommitState([_transient_error(), None])
        uow, database = _build_mongo_uow(state, seats=[_seat_document("seat-001")])
        result = uow.assign_or_move_if_active("seat-001", "stu-001", "cls-001")
        assert result.seat_id == "seat-001"
        assert state.attempts == 2
        assignments = database[MongoSeatMutationUnitOfWork.assignment_collection_name]
        assert assignments.find_one({"seat_id": "seat-001"}) is not None

    def test_transient_exhaustion_raises_503(self) -> None:
        state = FakeCommitState([_transient_error()] * 3)
        uow, _ = _build_mongo_uow(state, seats=[_seat_document("seat-001")])
        with pytest.raises(RepositoryUnavailableError) as exc_info:
            uow.assign_or_move_if_active("seat-001", "stu-001", "cls-001")
        assert exc_info.value.status_code == 503
        assert state.attempts == 3

    def test_ambiguous_commit_with_verified_outcome_returns_success(self) -> None:
        # commit 불확실이어도 재조회로 성공 상태가 확정되면 성공으로 처리한다.
        state = FakeCommitState([_ambiguous_commit_error()])
        uow, database = _build_mongo_uow(state, seats=[_seat_document("seat-001")])
        result = uow.assign_or_move_if_active("seat-001", "stu-001", "cls-001")
        assert result.seat_id == "seat-001"
        assert result.student_id == "stu-001"
        assignments = database[MongoSeatMutationUnitOfWork.assignment_collection_name]
        assert assignments.find_one({"seat_id": "seat-001"}) is not None
        assert state.attempts == 1

    def test_ambiguous_autoreconnect_is_not_transient(self) -> None:
        """commit 시점 AutoReconnect는 transient가 아니라 commit 불확실로 분류한다.

        isinstance 기반 판정이 우선하면 재조회를 건너뛰므로,
        UnknownTransactionCommitResult label이 있으면 transient에서 제외한다.
        """
        error = _ambiguous_autoreconnect_error()
        assert _is_ambiguous_commit_error(error)
        assert not _is_transient_transaction_error(error)

    def test_commit_uncertainty_is_checked_before_transient_for_autoreconnect(self) -> None:
        """AutoReconnect + UnknownTransactionCommitResult는 재조회로 확정을 확인한다.

        이전에는 isinstance 기반 transient가 우선해 재시도만 하고 재조회를
        건너뛰었지만, commit 불확실을 먼저 검사해 재조회로 성공을 확정한다.
        """
        state = FakeCommitState([_ambiguous_autoreconnect_error()])
        uow, database = _build_mongo_uow(state, seats=[_seat_document("seat-001")])
        result = uow.assign_or_move_if_active("seat-001", "stu-001", "cls-001")
        assert result.seat_id == "seat-001"
        assert result.student_id == "stu-001"
        # 재시도 없이 재조회로 확정 확인 후 성공 반환
        assert state.attempts == 1
        assignments = database[MongoSeatMutationUnitOfWork.assignment_collection_name]
        assert assignments.find_one({"seat_id": "seat-001"}) is not None

    def test_delete_ambiguous_commit_with_verified_absence_returns_success(self) -> None:
        """delete의 commit 불확실도 재조회로 삭제 확정을 확인하면 성공 처리한다."""
        state = FakeCommitState([_ambiguous_commit_error()])
        uow, database = _build_mongo_uow(
            state,
            seats=[_seat_document("seat-001")],
            assignments=[_assignment_document("seat-001", "stu-001")],
        )
        uow.delete_seat_and_unassign("seat-001")
        seats = database[MongoClassroomRepository.seat_collection_name]
        assignments = database[MongoSeatMutationUnitOfWork.assignment_collection_name]
        # 부재(absence)를 seat·assignment 양쪽 read로 확인해 성공 처리한다.
        assert seats.find_one({"_id": "seat-001"}) is None
        assert assignments.find_one({"seat_id": "seat-001"}) is None
        assert state.attempts == 1

    def test_unassign_ambiguous_commit_with_verified_absence_returns_success(self) -> None:
        """unassign의 commit 불확실도 재조회로 해제 확정을 확인하면 성공 처리한다."""
        state = FakeCommitState([_ambiguous_commit_error()])
        uow, database = _build_mongo_uow(
            state,
            seats=[_seat_document("seat-001")],
            assignments=[_assignment_document("seat-001", "stu-001")],
        )
        uow.unassign_if_active("seat-001")
        assignments = database[MongoSeatMutationUnitOfWork.assignment_collection_name]
        assert assignments.find_one({"seat_id": "seat-001"}) is None
        assert state.attempts == 1

    def test_ambiguous_commit_with_absence_confirmed_raises_503(self) -> None:
        # commit 불확실 + 재조회로 absence만 확인 → 재시도 소진 후 503.
        state = FakeCommitState([_ambiguous_commit_error()] * 3)
        uow, _ = _build_mongo_uow(
            state, seats=[_seat_document("seat-001")], assignments_persist=False
        )
        with pytest.raises(RepositoryUnavailableError) as exc_info:
            uow.assign_or_move_if_active("seat-001", "stu-001", "cls-001")
        assert exc_info.value.status_code == 503
        assert state.attempts == 3

    def test_unexpected_mongo_error_raises_503(self) -> None:
        state = FakeCommitState([OperationFailure("unexpected failure")])
        uow, _ = _build_mongo_uow(state, seats=[_seat_document("seat-001")])
        with pytest.raises(RepositoryUnavailableError) as exc_info:
            uow.assign_or_move_if_active("seat-001", "stu-001", "cls-001")
        assert exc_info.value.status_code == 503
        assert state.attempts == 1

    def test_start_session_transient_failure_retries(self) -> None:
        """start_session이 transient로 실패하면 재시도한다."""
        state = FakeCommitState([])
        database = FakeDatabase({"setName": "rs0", "isWritablePrimary": True}, state)
        database[MongoClassroomRepository.seat_collection_name] = FakeCollection(
            [_seat_document("seat-001")]
        )

        class FlakyClient(FakeClient):
            def __init__(self, commit_state: FakeCommitState) -> None:
                super().__init__(commit_state)
                self.start_attempts = 0

            def start_session(self) -> FakeSession:
                self.start_attempts += 1
                if self.start_attempts == 1:
                    raise AutoReconnect("connection lost")
                return FakeSession(self._state)

        client = FlakyClient(state)
        database._client = client
        repository = MongoClassroomRepository(database)  # type: ignore[arg-type]
        uow = MongoSeatMutationUnitOfWork(repository, database, clock=_fixed_now)  # type: ignore[arg-type]

        result = uow.assign_or_move_if_active("seat-001", "stu-001", "cls-001")

        assert result.seat_id == "seat-001"
        assert client.start_attempts == 2

    def test_start_session_unexpected_failure_raises_503(self) -> None:
        """start_session의 예상 밖 실패는 503으로 변환한다."""
        state = FakeCommitState([])
        database = FakeDatabase({"setName": "rs0", "isWritablePrimary": True}, state)
        database[MongoClassroomRepository.seat_collection_name] = FakeCollection(
            [_seat_document("seat-001")]
        )

        class FailingClient(FakeClient):
            def start_session(self) -> FakeSession:
                raise OperationFailure("unexpected session failure")

        database._client = FailingClient(state)
        repository = MongoClassroomRepository(database)  # type: ignore[arg-type]
        uow = MongoSeatMutationUnitOfWork(repository, database, clock=_fixed_now)  # type: ignore[arg-type]

        with pytest.raises(RepositoryUnavailableError) as exc_info:
            uow.assign_or_move_if_active("seat-001", "stu-001", "cls-001")
        assert exc_info.value.status_code == 503


# ============================================================
# mongo 지정 저장소: UoW 쓰기와 읽기 경로 일관성 (Finding 1)
# ============================================================


class TestMongoAssignmentRepositoryReadsUowWrites:
    def test_reads_see_uow_written_assignments(self) -> None:
        """UoW가 transaction으로 쓴 지정을 저장소 조회가 그대로 읽는다."""
        state = FakeCommitState([None])
        uow, database = _build_mongo_uow(state, seats=[_seat_document("seat-001")])
        repository = MongoSeatAssignmentRepository(database)  # type: ignore[arg-type]

        uow.assign_or_move_if_active("seat-001", "stu-001", "cls-001")

        assignments = repository.list_by_classroom("cls-001")
        assert len(assignments) == 1
        assert assignments[0].seat_id == "seat-001"
        assert assignments[0].student_id == "stu-001"
        assert assignments[0].classroom_id == "cls-001"
        assert repository.list_by_classroom("cls-999") == []

        by_seat = repository.get_by_seat("seat-001")
        assert by_seat is not None
        assert by_seat.student_id == "stu-001"
        assert repository.get_by_seat("seat-999") is None

        by_student = repository.get_by_student("stu-001", "cls-001")
        assert by_student is not None
        assert by_student.seat_id == "seat-001"
        assert repository.get_by_student("stu-999", "cls-001") is None

    def test_unassign_by_student_removes_uow_written_assignments(self) -> None:
        """UoW가 쓴 지정을 학생 단위 해제로 지우고 해제 수를 반환한다."""
        state = FakeCommitState([None, None])
        uow, database = _build_mongo_uow(
            state,
            seats=[_seat_document("seat-001"), _seat_document("seat-002")],
        )
        repository = MongoSeatAssignmentRepository(database)  # type: ignore[arg-type]
        uow.assign_or_move_if_active("seat-001", "stu-001", "cls-001")
        uow.assign_or_move_if_active("seat-002", "stu-002", "cls-001")

        count = repository.unassign_by_student("stu-001")

        assert count == 1
        remaining = repository.list_by_classroom("cls-001")
        assert len(remaining) == 1
        assert remaining[0].seat_id == "seat-002"
        assert remaining[0].student_id == "stu-002"

    def test_direct_assign_and_get_roundtrip(self) -> None:
        """레거시 직접 assign/unassign 경로도 같은 컬렉션에 쓰고 읽는다."""
        state = FakeCommitState([])
        _, database = _build_mongo_uow(state)
        repository = MongoSeatAssignmentRepository(database)  # type: ignore[arg-type]

        assignment = SeatAssignment(
            seat_id="seat-001",
            student_id="stu-001",
            classroom_id="cls-001",
            assigned_at=_FIXED_NOW,
        )
        assert repository.assign(assignment) == assignment
        assert repository.get_by_seat("seat-001") == assignment
        repository.unassign("seat-001")
        assert repository.get_by_seat("seat-001") is None


# ============================================================
# 저장소 간 race parity (동일 선형화 규칙)
# ============================================================


class TestStoreParity:
    def test_same_operation_sequence_produces_same_final_state(self) -> None:
        # memory
        store = _build_memory_store()
        memory_uow = InMemorySeatMutationUnitOfWork(store, clock=_fixed_now)
        memory_uow.assign_or_move_if_active("seat-001", "stu-001", "cls-001")
        memory_uow.assign_or_move_if_active("seat-002", "stu-001", "cls-001")
        memory_uow.unassign_if_active("seat-002")
        memory_uow.delete_seat_and_unassign("seat-001")
        memory_state = {
            "seat-001_present": store.get_seat("seat-001") is not None,
            "seat-001_assignment": store.get_assignment_by_seat("seat-001"),
            "seat-002_assignment": store.get_assignment_by_seat("seat-002"),
        }

        # mongo (fake)
        state = FakeCommitState([None, None, None, None])
        mongo_uow, database = _build_mongo_uow(
            state,
            seats=[_seat_document("seat-001"), _seat_document("seat-002")],
        )
        mongo_uow.assign_or_move_if_active("seat-001", "stu-001", "cls-001")
        mongo_uow.assign_or_move_if_active("seat-002", "stu-001", "cls-001")
        mongo_uow.unassign_if_active("seat-002")
        mongo_uow.delete_seat_and_unassign("seat-001")
        seats = database[MongoClassroomRepository.seat_collection_name]
        assignments = database[MongoSeatMutationUnitOfWork.assignment_collection_name]
        mongo_state = {
            "seat-001_present": seats.find_one({"_id": "seat-001"}) is not None,
            "seat-001_assignment": assignments.find_one({"seat_id": "seat-001"}),
            "seat-002_assignment": assignments.find_one({"seat_id": "seat-002"}),
        }

        assert memory_state["seat-001_present"] == mongo_state["seat-001_present"]
        assert (memory_state["seat-001_assignment"] is None) == (
            mongo_state["seat-001_assignment"] is None
        )
        assert (memory_state["seat-002_assignment"] is None) == (
            mongo_state["seat-002_assignment"] is None
        )
        # hard delete 후에는 두 저장소 모두 좌석·지정이 남지 않는다.
        assert memory_state["seat-001_present"] is False
        assert memory_state["seat-001_assignment"] is None
        assert memory_state["seat-002_assignment"] is None
        # 다른 좌석(seat-002)은 두 저장소 모두 그대로 남는다.
        assert store.get_seat("seat-002") is not None
        assert seats.find_one({"_id": "seat-002"}) is not None
