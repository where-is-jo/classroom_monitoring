"""TASK-006 hard delete 완료 기준 커버리지.

- delete와 observation의 경쟁(사전 검증·claim 이후·실제 스레드 race)
- observation-first commit 후 hard delete가 history·batch를 보존하는지
- `append_history_and_apply_occupancy`의 memory/mongo 선형화 규칙 동일성
- transient 3회·commit 불확실 시 delete의 재조회·503 정책
- HTTP 계약: DELETE 204/no body, 반복·후속 mutation 404 SEAT_NOT_FOUND
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime
from threading import Barrier, Event, Thread

import pytest
from fastapi.testclient import TestClient
from pymongo.errors import AutoReconnect, OperationFailure

from app.classrooms.adapters.memory_repository import (
    InMemoryClassroomRepository,
    InMemorySeatAssignmentRepository,
    InMemorySeatMutationUnitOfWork,
)
from app.classrooms.adapters.mongo_repository import (
    MongoClassroomRepository,
    MongoSeatMutationUnitOfWork,
)
from app.classrooms.errors import SeatNotFoundError
from app.classrooms.models import (
    Classroom,
    ObservationBatchStatus,
    OccupancySource,
    Seat,
    SeatCurrentOccupancy,
    SeatObservation,
    SeatOccupancy,
    SeatOccupancyHistory,
)
from app.classrooms.service import ClassroomService
from app.main import app
from app.shared.adapters.memory_student_lookup import InMemoryStudentLookup
from app.shared.dependencies import get_classroom_service
from app.shared.errors import ClassroomInputError, RepositoryUnavailableError
from app.shared.student_identity import StudentIdentity

from .test_uow import (
    FakeCollection,
    FakeCommitState,
    FakeDatabase,
    FakeWriteResult,
    _ambiguous_commit_error,
    _assignment_document,
    _build_mongo_uow,
    _transient_error,
)

_NOW = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)


def _fixed_now() -> datetime:
    return _NOW


# ============================================================
# memory fixture (관측까지 다루는 서비스)
# ============================================================


def _build_store() -> InMemoryClassroomRepository:
    store = InMemoryClassroomRepository()
    store.create_classroom(
        Classroom(
            id="cls-001",
            code="R101",
            name="강의실1",
            location="본관",
            is_active=True,
            created_at=_NOW,
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
                created_at=_NOW,
                updated_at=_NOW,
                version=0,
            )
        )
    return store


def _build_service(
    store: InMemoryClassroomRepository,
    uow: InMemorySeatMutationUnitOfWork,
    *,
    clock: Callable[[], datetime] = _fixed_now,
) -> ClassroomService:
    student_lookup = InMemoryStudentLookup(
        identities=(
            StudentIdentity(id="stu-001", student_no="20260101", name="홍길동", is_active=True),
        )
    )
    return ClassroomService(
        store,
        student_lookup=student_lookup,
        assignment_repository=InMemorySeatAssignmentRepository(store=store),
        uow=uow,
        occupancy_confidence_threshold=0.6,
        clock=clock,
    )


def _memory_service() -> tuple[
    InMemoryClassroomRepository, InMemorySeatMutationUnitOfWork, ClassroomService
]:
    store = _build_store()
    uow = InMemorySeatMutationUnitOfWork(store, clock=_fixed_now)
    return store, uow, _build_service(store, uow)


def _record(
    service: ClassroomService,
    *,
    event_id: str,
    seat_ids: tuple[str, ...] = ("seat-001",),
) -> None:
    service.record_seat_observation_batch(
        event_id=event_id,
        classroom_id="cls-001",
        observations=tuple(
            SeatObservation(seat_id=seat_id, occupied=True, confidence=0.95) for seat_id in seat_ids
        ),
        observed_at=_NOW,
    )


class GatedClock:
    """지정한 호출 순번에서 멈춰 다른 스레드가 끼어들 창을 만드는 시계.

    서비스는 ``append_history_and_apply_occupancy``의 ``updated_at`` 인자를
    만들 때 시계를 읽는다. 그 시점에 멈추면 batch claim과 좌석 조회는 이미
    끝났지만 history는 아직 기록되지 않은 정확한 지점이 되고, 여기서 다른
    스레드의 hard delete를 실행해 실제 경쟁을 결정적으로 재현할 수 있다.
    """

    def __init__(self, *, pause_at_call: int) -> None:
        self._pause_at_call = pause_at_call
        self._calls = 0
        self.reached = Event()
        self.resume = Event()

    def __call__(self) -> datetime:
        self._calls += 1
        if self._calls == self._pause_at_call:
            self.reached.set()
            assert self.resume.wait(timeout=10), "delete 스레드가 제때 끝나지 않았습니다."
        return _NOW


def _observe_with_delete_at(
    store: InMemoryClassroomRepository,
    uow: InMemorySeatMutationUnitOfWork,
    *,
    pause_at_call: int,
    event_id: str,
    seat_ids: tuple[str, ...],
    deleted_seat_id: str,
) -> Exception | None:
    """관측을 진행하다 지정한 지점에서 hard delete를 끼워 넣고 결과를 돌려준다."""
    clock = GatedClock(pause_at_call=pause_at_call)
    observing = _build_service(store, uow, clock=clock)
    deleting = _build_service(store, uow)
    outcome: list[Exception | None] = []

    def run_observation() -> None:
        try:
            _record(observing, event_id=event_id, seat_ids=seat_ids)
            outcome.append(None)
        except Exception as exc:
            outcome.append(exc)

    thread = Thread(target=run_observation)
    thread.start()
    try:
        assert clock.reached.wait(timeout=10), "관측이 지정한 지점에 도달하지 않았습니다."
        deleting.delete_seat(deleted_seat_id)
    finally:
        clock.resume.set()
        thread.join(timeout=10)
    assert not thread.is_alive()
    return outcome[0]


# ============================================================
# delete-first observation (사전 검증 단계)
# ============================================================


class TestDeleteFirstObservationBeforeClaim:
    def test_observation_for_deleted_seat_returns_422_without_batch_or_history(self) -> None:
        """hard delete된 좌석의 관측은 422이고 batch·history를 남기지 않는다."""
        store, _, service = _memory_service()
        service.delete_seat("seat-001")

        with pytest.raises(ClassroomInputError) as exc_info:
            _record(service, event_id="evt-1")

        assert exc_info.value.status_code == 422
        assert exc_info.value.code == "CLASSROOM_INPUT_INVALID"
        assert store.get_observation_batch("evt-1") is None
        assert store.get_history_by_event_and_seat("evt-1", "seat-001") is None

    def test_batch_with_one_deleted_seat_is_rejected_as_a_whole(self) -> None:
        """batch 안에 삭제된 좌석이 하나라도 있으면 생존 좌석도 갱신되지 않는다."""
        store, _, service = _memory_service()
        service.delete_seat("seat-001")

        with pytest.raises(ClassroomInputError):
            _record(service, event_id="evt-1", seat_ids=("seat-002", "seat-001"))

        assert store.get_observation_batch("evt-1") is None
        assert store.get_history_by_event_and_seat("evt-1", "seat-002") is None
        survivor = store.get_seat("seat-002")
        assert survivor is not None
        assert survivor.current_occupancy.state is SeatOccupancy.UNKNOWN
        assert survivor.version == 0

    def test_retry_of_same_event_stays_422(self) -> None:
        """같은 event_id로 재시도해도 결과가 422로 멱등하다."""
        _, _, service = _memory_service()
        service.delete_seat("seat-001")

        for _ in range(2):
            with pytest.raises(ClassroomInputError):
                _record(service, event_id="evt-1")


# ============================================================
# delete-first observation (claim 이후 interleaving)
# ============================================================


class TestDeleteBetweenClaimAndApply:
    def test_delete_after_claim_returns_422_and_writes_nothing(self) -> None:
        """claim 이후 delete가 끼어들면 422이고 history·current가 남지 않는다.

        UoW가 좌석 부재를 확인하고 아무것도 쓰지 않은 채
        ``SeatNotFoundError``를 올리며, 서비스가 사전 검증과 같은 422로
        수렴시킨다 (두 저장소 동일).
        """
        store = _build_store()
        uow = InMemorySeatMutationUnitOfWork(store, clock=_fixed_now)

        outcome = _observe_with_delete_at(
            store,
            uow,
            pause_at_call=2,  # claim 이후, history 기록 직전
            event_id="evt-1",
            seat_ids=("seat-001",),
            deleted_seat_id="seat-001",
        )

        assert isinstance(outcome, ClassroomInputError)
        assert outcome.status_code == 422
        assert outcome.code == "CLASSROOM_INPUT_INVALID"
        assert store.get_seat("seat-001") is None
        assert store.get_history_by_event_and_seat("evt-1", "seat-001") is None

    def test_batch_stays_processing_and_retry_is_still_422(self) -> None:
        """claim된 batch는 COMPLETED로 넘어가지 않고, 재시도도 같은 422다."""
        store = _build_store()
        uow = InMemorySeatMutationUnitOfWork(store, clock=_fixed_now)

        outcome = _observe_with_delete_at(
            store,
            uow,
            pause_at_call=2,
            event_id="evt-1",
            seat_ids=("seat-001",),
            deleted_seat_id="seat-001",
        )

        assert isinstance(outcome, ClassroomInputError)
        claimed = store.get_observation_batch("evt-1")
        assert claimed is not None
        assert claimed.status is ObservationBatchStatus.PROCESSING
        assert claimed.processed_count == 0
        assert claimed.changed_count == 0
        assert claimed.completed_at is None

        # 재요청은 사전 검증에서 다시 422가 되고 batch 상태도 그대로다.
        with pytest.raises(ClassroomInputError):
            _record(_build_service(store, uow), event_id="evt-1")
        again = store.get_observation_batch("evt-1")
        assert again is not None
        assert again.status is ObservationBatchStatus.PROCESSING

    def test_batch_중간_delete면_같은_batch의_다른_좌석도_쓰이지_않는다(self) -> None:
        """batch 하나가 통째로 성립하거나 아무것도 쓰지 않는다.

        **예전에는 좌석마다 transaction을 열어 앞쪽 좌석이 이미 커밋됐다.** 그러면
        batch record는 processed_count=0인데 일부 좌석 history만 남아, 저장된 것과
        batch 상태가 어긋난다. 좌석 관측을 한 transaction으로 묶으면서(결정 0046)
        실패 단위도 batch가 됐다.
        """
        store = _build_store()
        uow = InMemorySeatMutationUnitOfWork(store, clock=_fixed_now)

        outcome = _observe_with_delete_at(
            store,
            uow,
            pause_at_call=2,
            event_id="evt-1",
            seat_ids=("seat-002", "seat-001"),
            deleted_seat_id="seat-001",
        )

        assert isinstance(outcome, ClassroomInputError)
        assert store.get_seat("seat-001") is None
        assert store.get_history_by_event_and_seat("evt-1", "seat-001") is None
        # 같은 batch의 다른 좌석도 쓰이지 않는다.
        assert store.get_history_by_event_and_seat("evt-1", "seat-002") is None
        survivor = store.get_seat("seat-002")
        assert survivor is not None
        assert survivor.current_occupancy.state is SeatOccupancy.UNKNOWN


# ============================================================
# 실제 스레드 race (memory 공유 lock)
# ============================================================


def _run_delete_observation_race(event_id: str) -> None:
    """delete와 관측 batch를 실제로 동시에 실행하고 불변식을 검사한다."""
    store, uow, service = _memory_service()
    uow.assign_or_move_if_active("seat-001", "stu-001", "cls-001")
    barrier = Barrier(2)
    failures: list[Exception] = []
    observed: list[Exception | None] = []

    def do_delete() -> None:
        barrier.wait()
        try:
            service.delete_seat("seat-001")
        except Exception as exc:  # pragma: no cover - 실패 시 진단용
            failures.append(exc)

    def do_observe() -> None:
        barrier.wait()
        try:
            _record(service, event_id=event_id)
            observed.append(None)
        except Exception as exc:
            observed.append(exc)

    threads = [Thread(target=do_delete), Thread(target=do_observe)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # delete는 항상 성공하고, 좌석·지정이 함께 사라진다 (고아 지정 없음).
    assert failures == []
    assert store.get_seat("seat-001") is None
    assert store.get_assignment_by_seat("seat-001") is None

    outcome = observed[0]
    batch = store.get_observation_batch(event_id)
    history = store.get_history_by_event_and_seat(event_id, "seat-001")
    if outcome is None:
        # observation-first: history가 남고 batch가 완료된다.
        assert history is not None
        assert batch is not None
        assert batch.status is ObservationBatchStatus.COMPLETED
    else:
        # delete-first: 422이고 history도 batch 완료도 없다.
        assert isinstance(outcome, ClassroomInputError)
        assert outcome.status_code == 422
        assert history is None
        assert batch is None or batch.status is ObservationBatchStatus.PROCESSING


class TestConcurrentDeleteAndObservation:
    def test_concurrent_delete_and_observation_batch_is_linearized(self) -> None:
        """delete와 관측이 실제로 겹쳐도 결과가 두 갈래로만 수렴한다.

        - 관측이 먼저 커밋되면 batch가 COMPLETED이고 history가 남는다.
        - delete가 먼저면 422이고 그 좌석의 history·batch 완료가 없다.
        어느 쪽이든 좌석·지정은 함께 사라지고 고아 상태가 남지 않는다.
        """
        for attempt in range(20):
            _run_delete_observation_race(f"evt-race-{attempt}")


# ============================================================
# observation-first commit 후 hard delete (history 보존)
# ============================================================


class TestObservationFirstThenDelete:
    def test_history_and_batch_survive_hard_delete_in_memory(self) -> None:
        """관측이 먼저 커밋되면 이후 hard delete가 history·batch를 지우지 않는다."""
        store, _, service = _memory_service()
        _record(service, event_id="evt-1")
        history = store.get_history_by_event_and_seat("evt-1", "seat-001")
        assert history is not None
        seat = store.get_seat("seat-001")
        assert seat is not None
        assert seat.current_occupancy.state is SeatOccupancy.OCCUPIED

        service.delete_seat("seat-001")

        assert store.get_seat("seat-001") is None
        assert store.get_history_by_event_and_seat("evt-1", "seat-001") == history
        batch = store.get_observation_batch("evt-1")
        assert batch is not None
        assert batch.status is ObservationBatchStatus.COMPLETED

    def test_history_written_by_uow_survives_hard_delete_in_mongo(self) -> None:
        """mongo UoW가 쓴 history 문서도 hard delete 후 그대로 남는다."""
        state = FakeCommitState([None, None])
        uow, database = _build_mongo_uow(
            state,
            seats=[_versioned_seat_document("seat-001")],
            assignments=[_assignment_document("seat-001", "stu-001")],
        )
        stored = uow.append_history_and_apply_occupancy(
            _history("seat-001"),
            expected_version=0,
            occupancy=_occupancy(),
            updated_at=_NOW,
        )
        assert stored is not None

        uow.delete_seat_and_unassign("seat-001")

        seats = database[MongoClassroomRepository.seat_collection_name]
        history = database[MongoClassroomRepository.history_collection_name]
        assignments = database[MongoSeatMutationUnitOfWork.assignment_collection_name]
        assert seats.find_one({"_id": "seat-001"}) is None
        assert assignments.find_one({"seat_id": "seat-001"}) is None
        assert history.find_one({"event_id": "evt-1", "seat_id": "seat-001"}) is not None
        assert history.delete_calls == []


# ============================================================
# append_history_and_apply_occupancy 선형화 규칙 (memory/mongo 동일)
# ============================================================


def _versioned_seat_document(
    seat_id: str, *, version: int = 0, is_active: bool = True
) -> dict[str, object]:
    return {
        "_id": seat_id,
        "classroom_id": "cls-001",
        "code": f"S-{seat_id}",
        "is_active": is_active,
        "version": version,
    }


def _history(seat_id: str, *, event_id: str = "evt-1") -> SeatOccupancyHistory:
    return SeatOccupancyHistory(
        id=f"hist-{event_id}-{seat_id}",
        seat_id=seat_id,
        classroom_id="cls-001",
        event_id=event_id,
        source=OccupancySource.SYSTEM,
        from_state=SeatOccupancy.UNKNOWN,
        to_state=SeatOccupancy.OCCUPIED,
        occupied=True,
        confidence=0.95,
        observed_at=_NOW,
        received_at=_NOW,
        applied_to_current=True,
        state_changed=True,
    )


def _occupancy() -> SeatCurrentOccupancy:
    return SeatCurrentOccupancy(
        state=SeatOccupancy.OCCUPIED,
        source=OccupancySource.SYSTEM,
        confidence=0.95,
        observed_at=_NOW,
        event_id="evt-1",
    )


class TestAppendHistoryLinearization:
    def test_memory_deleted_seat_raises_not_found_and_writes_nothing(self) -> None:
        store = _build_store()
        uow = InMemorySeatMutationUnitOfWork(store, clock=_fixed_now)
        uow.delete_seat_and_unassign("seat-001")

        with pytest.raises(SeatNotFoundError):
            uow.append_history_and_apply_occupancy(
                _history("seat-001"),
                expected_version=0,
                occupancy=_occupancy(),
                updated_at=_NOW,
            )

        assert store.get_history_by_event_and_seat("evt-1", "seat-001") is None

    def test_memory_inactive_seat_raises_not_found(self) -> None:
        """legacy inactive 좌석도 관측 적용 대상이 아니다."""
        store = _build_store()
        seat = store.get_seat("seat-001")
        assert seat is not None
        store.update_seat(replace(seat, is_active=False))
        uow = InMemorySeatMutationUnitOfWork(store, clock=_fixed_now)

        with pytest.raises(SeatNotFoundError):
            uow.append_history_and_apply_occupancy(
                _history("seat-001"),
                expected_version=0,
                occupancy=_occupancy(),
                updated_at=_NOW,
            )

        assert store.get_history_by_event_and_seat("evt-1", "seat-001") is None

    def test_memory_version_mismatch_returns_none_without_writes(self) -> None:
        store = _build_store()
        uow = InMemorySeatMutationUnitOfWork(store, clock=_fixed_now)

        assert (
            uow.append_history_and_apply_occupancy(
                _history("seat-001"),
                expected_version=7,
                occupancy=_occupancy(),
                updated_at=_NOW,
            )
            is None
        )
        assert store.get_history_by_event_and_seat("evt-1", "seat-001") is None
        seat = store.get_seat("seat-001")
        assert seat is not None
        assert seat.version == 0

    def test_memory_without_occupancy_appends_history_only(self) -> None:
        store = _build_store()
        uow = InMemorySeatMutationUnitOfWork(store, clock=_fixed_now)

        stored = uow.append_history_and_apply_occupancy(
            _history("seat-001"),
            expected_version=0,
            occupancy=None,
            updated_at=_NOW,
        )

        assert stored is not None
        seat = store.get_seat("seat-001")
        assert seat is not None
        assert seat.version == 0
        assert seat.current_occupancy.state is SeatOccupancy.UNKNOWN

    def test_mongo_deleted_seat_raises_not_found_and_writes_nothing(self) -> None:
        state = FakeCommitState([])
        uow, database = _build_mongo_uow(state)

        with pytest.raises(SeatNotFoundError):
            uow.append_history_and_apply_occupancy(
                _history("seat-001"),
                expected_version=0,
                occupancy=_occupancy(),
                updated_at=_NOW,
            )

        history = database[MongoClassroomRepository.history_collection_name]
        seats = database[MongoClassroomRepository.seat_collection_name]
        assert history.insert_calls == []
        assert seats.update_calls == []
        assert state.commits == 0

    def test_mongo_inactive_seat_raises_not_found(self) -> None:
        state = FakeCommitState([])
        uow, database = _build_mongo_uow(
            state, seats=[_versioned_seat_document("seat-001", is_active=False)]
        )

        with pytest.raises(SeatNotFoundError):
            uow.append_history_and_apply_occupancy(
                _history("seat-001"),
                expected_version=0,
                occupancy=_occupancy(),
                updated_at=_NOW,
            )

        assert database[MongoClassroomRepository.history_collection_name].insert_calls == []

    def test_mongo_version_mismatch_returns_none_without_writes(self) -> None:
        state = FakeCommitState([None])
        uow, database = _build_mongo_uow(state, seats=[_versioned_seat_document("seat-001")])

        assert (
            uow.append_history_and_apply_occupancy(
                _history("seat-001"),
                expected_version=7,
                occupancy=_occupancy(),
                updated_at=_NOW,
            )
            is None
        )
        assert database[MongoClassroomRepository.history_collection_name].insert_calls == []
        assert database[MongoClassroomRepository.seat_collection_name].update_calls == []

    def test_mongo_applies_history_and_current_in_one_transaction(self) -> None:
        state = FakeCommitState([None])
        uow, database = _build_mongo_uow(state, seats=[_versioned_seat_document("seat-001")])

        stored = uow.append_history_and_apply_occupancy(
            _history("seat-001"),
            expected_version=0,
            occupancy=_occupancy(),
            updated_at=_NOW,
        )

        assert stored is not None
        seats = database[MongoClassroomRepository.seat_collection_name]
        document = seats.find_one({"_id": "seat-001"})
        assert document is not None
        assert document["version"] == 1
        assert document["current_occupancy"] == {
            "state": "OCCUPIED",
            "source": "SYSTEM",
            "confidence": 0.95,
            "observed_at": _NOW,
            "event_id": "evt-1",
        }
        assert state.commits == 1

    def test_mongo_existing_history_is_returned_without_duplicate_insert(self) -> None:
        """같은 (event_id, seat_id)는 한 번만 기록된다 (재시도 멱등)."""
        state = FakeCommitState([None, None])
        uow, database = _build_mongo_uow(state, seats=[_versioned_seat_document("seat-001")])
        first = uow.append_history_and_apply_occupancy(
            _history("seat-001"),
            expected_version=0,
            occupancy=_occupancy(),
            updated_at=_NOW,
        )

        second = uow.append_history_and_apply_occupancy(
            _history("seat-001"),
            expected_version=1,
            occupancy=None,
            updated_at=_NOW,
        )

        assert first == second
        history = database[MongoClassroomRepository.history_collection_name]
        assert len(history.insert_calls) == 1


# ============================================================
# delete의 transient retry / commit 불확실 정책
# ============================================================


class RollbackAwareCollection(FakeCollection):
    """commit이 실패할 시도의 쓰기를 반영하지 않는 컬렉션 fake.

    다음 commit 동작이 예외로 예약되어 있으면 그 시도의 쓰기를 버려
    transaction rollback을 흉내낸다. delete·history append처럼 재시도가
    멱등하지 않은 연산의 transient retry를 사실대로 검증하기 위해 필요하다.
    """

    def __init__(self, documents: list[dict[str, object]], state: FakeCommitState) -> None:
        super().__init__(documents)
        self._state = state

    def _sync_persistence(self) -> None:
        upcoming = self._state.behaviors[0] if self._state.behaviors else None
        self.persist_writes = upcoming is None

    def insert_one(self, document: dict[str, object], session: object = None) -> None:
        self._sync_persistence()
        super().insert_one(document, session=session)

    def delete_one(self, query: dict[str, object], session: object = None) -> FakeWriteResult:
        self._sync_persistence()
        return super().delete_one(query, session=session)

    def update_one(
        self,
        query: dict[str, object],
        update: dict[str, object],
        session: object = None,
    ) -> FakeWriteResult:
        self._sync_persistence()
        return super().update_one(query, update, session=session)

    def replace_one(
        self,
        query: dict[str, object],
        replacement: dict[str, object],
        upsert: bool = False,
        session: object = None,
    ) -> FakeWriteResult:
        self._sync_persistence()
        return super().replace_one(query, replacement, upsert=upsert, session=session)


def _build_rollback_aware_uow(
    state: FakeCommitState,
    *,
    seats: list[dict[str, object]],
    assignments: list[dict[str, object]] | None = None,
) -> tuple[MongoSeatMutationUnitOfWork, FakeDatabase]:
    database = FakeDatabase(
        {"setName": "rs0", "isWritablePrimary": True},
        state,
        collections={
            MongoSeatMutationUnitOfWork.assignment_collection_name: RollbackAwareCollection(
                list(assignments or []), state
            )
        },
    )
    database[MongoClassroomRepository.seat_collection_name] = RollbackAwareCollection(seats, state)
    database[MongoClassroomRepository.history_collection_name] = RollbackAwareCollection([], state)
    repository = MongoClassroomRepository(database)  # type: ignore[arg-type]
    uow = MongoSeatMutationUnitOfWork(repository, database, clock=_fixed_now)  # type: ignore[arg-type]
    return uow, database


class TestDeleteRetryPolicy:
    def test_delete_transient_retries_then_succeeds(self) -> None:
        """첫 시도가 rollback되어도 재시도가 좌석·지정을 지운다."""
        state = FakeCommitState([_transient_error(), None])
        uow, database = _build_rollback_aware_uow(
            state,
            seats=[_versioned_seat_document("seat-001")],
            assignments=[_assignment_document("seat-001", "stu-001")],
        )

        uow.delete_seat_and_unassign("seat-001")

        assert state.attempts == 2
        seats = database[MongoClassroomRepository.seat_collection_name]
        assignments = database[MongoSeatMutationUnitOfWork.assignment_collection_name]
        assert seats.find_one({"_id": "seat-001"}) is None
        assert assignments.find_one({"seat_id": "seat-001"}) is None

    def test_delete_transient_exhaustion_raises_503_after_three_attempts(self) -> None:
        state = FakeCommitState([_transient_error()] * 3)
        uow, _ = _build_mongo_uow(
            state, seats=[_versioned_seat_document("seat-001")], seats_persist=False
        )

        with pytest.raises(RepositoryUnavailableError) as exc_info:
            uow.delete_seat_and_unassign("seat-001")

        assert exc_info.value.status_code == 503
        assert state.attempts == 3

    def test_delete_ambiguous_commit_without_confirmed_absence_raises_503(self) -> None:
        """commit 불확실인데 seat·assignment 부재가 확인되지 않으면 503이다."""
        state = FakeCommitState([_ambiguous_commit_error()] * 3)
        uow, _ = _build_mongo_uow(
            state,
            seats=[_versioned_seat_document("seat-001")],
            assignments=[_assignment_document("seat-001", "stu-001")],
            seats_persist=False,
            assignments_persist=False,
        )

        with pytest.raises(RepositoryUnavailableError) as exc_info:
            uow.delete_seat_and_unassign("seat-001")

        assert exc_info.value.status_code == 503
        assert state.attempts == 3

    def test_delete_unexpected_error_raises_503_without_retry(self) -> None:
        state = FakeCommitState([OperationFailure("unexpected failure")])
        uow, _ = _build_mongo_uow(state, seats=[_versioned_seat_document("seat-001")])

        with pytest.raises(RepositoryUnavailableError):
            uow.delete_seat_and_unassign("seat-001")

        assert state.attempts == 1

    def test_append_history_transient_retries_then_succeeds(self) -> None:
        """rollback된 시도의 history·version 변화가 남지 않아 재시도가 성공한다."""
        state = FakeCommitState([AutoReconnect("primary not available"), None])
        uow, database = _build_rollback_aware_uow(
            state, seats=[_versioned_seat_document("seat-001")]
        )

        stored = uow.append_history_and_apply_occupancy(
            _history("seat-001"),
            expected_version=0,
            occupancy=_occupancy(),
            updated_at=_NOW,
        )

        assert stored is not None
        assert state.attempts == 2
        history = database[MongoClassroomRepository.history_collection_name]
        assert history.find_one({"event_id": "evt-1"}) is not None

    def test_append_history_ambiguous_commit_with_verified_write_succeeds(self) -> None:
        state = FakeCommitState([_ambiguous_commit_error()])
        uow, _ = _build_mongo_uow(state, seats=[_versioned_seat_document("seat-001")])

        stored = uow.append_history_and_apply_occupancy(
            _history("seat-001"),
            expected_version=0,
            occupancy=_occupancy(),
            updated_at=_NOW,
        )

        assert stored is not None
        assert state.attempts == 1


# ============================================================
# HTTP 계약: 204/no body, 반복·후속 mutation 404
# ============================================================


@pytest.fixture
def client() -> Iterator[TestClient]:
    _, _, service = _memory_service()
    app.dependency_overrides[get_classroom_service] = lambda: service
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


class TestHardDeleteHttpContract:
    def test_delete_returns_204_with_empty_body(self, client: TestClient) -> None:
        response = client.delete("/api/v1/classrooms/cls-001/seats/seat-001")
        assert response.status_code == 204
        assert response.content == b""

    def test_repeated_delete_returns_404_seat_not_found(self, client: TestClient) -> None:
        assert client.delete("/api/v1/classrooms/cls-001/seats/seat-001").status_code == 204
        repeated = client.delete("/api/v1/classrooms/cls-001/seats/seat-001")
        assert repeated.status_code == 404
        assert repeated.json()["error"]["code"] == "SEAT_NOT_FOUND"

    def test_mutations_after_delete_return_404_seat_not_found(self, client: TestClient) -> None:
        assert client.delete("/api/v1/classrooms/cls-001/seats/seat-001").status_code == 204

        updated = client.put(
            "/api/v1/classrooms/cls-001/seats/seat-001", json={"label": "이름 변경"}
        )
        assert updated.status_code == 404
        assert updated.json()["error"]["code"] == "SEAT_NOT_FOUND"

        assigned = client.put(
            "/api/v1/classrooms/cls-001/seats/seat-001/assignment",
            json={"student_id": "stu-001"},
        )
        assert assigned.status_code == 404
        assert assigned.json()["error"]["code"] == "SEAT_NOT_FOUND"

        released = client.delete("/api/v1/classrooms/cls-001/seats/seat-001/assignment")
        assert released.status_code == 404
        assert released.json()["error"]["code"] == "SEAT_NOT_FOUND"

        fetched = client.get("/api/v1/classrooms/cls-001/seats/seat-001")
        assert fetched.status_code == 404
        assert fetched.json()["error"]["code"] == "SEAT_NOT_FOUND"

    def test_delete_of_seat_in_other_classroom_returns_404(self, client: TestClient) -> None:
        other = client.post(
            "/api/v1/classrooms",
            json={"code": "R102", "name": "강의실2", "location": "별관"},
        )
        assert other.status_code == 201
        other_id = str(other.json()["id"])

        response = client.delete(f"/api/v1/classrooms/{other_id}/seats/seat-001")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "SEAT_NOT_FOUND"
        assert client.get("/api/v1/classrooms/cls-001/seats/seat-001").status_code == 200
