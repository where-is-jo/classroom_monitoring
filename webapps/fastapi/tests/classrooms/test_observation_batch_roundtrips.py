"""관측 batch가 저장소를 몇 번 왕복하는지 고정한다.

**이 테스트가 지키는 것은 정확성이 아니라 지연이다.** 저장소가 원격 MongoDB Atlas라
왕복 1회가 실측 약 42ms고, 좌석마다 따로 묻는 구현에서는 한 이벤트가 좌석 7개를
관측할 때 그것만으로 1초 가까이 들었다. 실제로 그래서 워커의 전송이 밀렸다
(결정 0043의 남은 일).

좌석 수에 비례해 늘어나는 호출은 `_apply_observation`의 좌석당 쓰기 하나뿐이어야
한다. 조회는 좌석 수와 무관하게 상수여야 한다.
"""

from __future__ import annotations

import collections
from datetime import UTC, datetime

import pytest

from app.classrooms.adapters.memory_repository import (
    InMemoryClassroomRepository,
    InMemorySeatMutationUnitOfWork,
)
from app.classrooms.models import (
    OccupancySource,
    RecordSeatObservationBatchCommand,
    SeatObservation,
)
from app.classrooms.service import ClassroomService

NOW = datetime(2026, 8, 26, 3, 0, tzinfo=UTC)


class CountingRepository(InMemoryClassroomRepository):
    """저장소 호출 횟수를 세는 대역. 동작은 그대로 둔다."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: collections.Counter[str] = collections.Counter()
        # 메모리 어댑터는 `append_occupancy_history` 안에서 조회를 다시 부른다.
        # 그것은 어댑터 내부 사정이라 저장소 왕복이 아니다. 서비스가 부른 것만 센다.
        self._inside_adapter = False

    def get_seat(self, seat_id: str):  # type: ignore[no-untyped-def]
        self.calls["get_seat"] += 1
        return super().get_seat(seat_id)

    def get_seats(self, seat_ids):  # type: ignore[no-untyped-def]
        self.calls["get_seats"] += 1
        return super().get_seats(seat_ids)

    def get_history_by_event_and_seat(self, event_id: str, seat_id: str):  # type: ignore[no-untyped-def]
        if not self._inside_adapter:
            self.calls["get_history_by_event_and_seat"] += 1
        return super().get_history_by_event_and_seat(event_id, seat_id)

    def get_histories_by_event(self, event_id: str):  # type: ignore[no-untyped-def]
        self.calls["get_histories_by_event"] += 1
        return super().get_histories_by_event(event_id)

    def append_occupancy_history(self, history):  # type: ignore[no-untyped-def]
        self.calls["append_occupancy_history"] += 1
        self._inside_adapter = True
        try:
            return super().append_occupancy_history(history)
        finally:
            self._inside_adapter = False


class CountingUnitOfWork(InMemorySeatMutationUnitOfWork):
    """UoW 호출 횟수를 세는 대역. 좌석당 transaction이 남아 있는지 보려는 것이다."""

    def __init__(self, store: InMemoryClassroomRepository) -> None:
        super().__init__(store)
        self.calls: collections.Counter[str] = collections.Counter()

    def append_history_and_apply_occupancy(self, history, **kwargs):  # type: ignore[no-untyped-def]
        self.calls["single"] += 1
        return super().append_history_and_apply_occupancy(history, **kwargs)

    def append_histories_and_apply_occupancies(self, applications, **kwargs):  # type: ignore[no-untyped-def]
        self.calls["batch"] += 1
        self.calls["batch_seats"] += len(applications)
        return super().append_histories_and_apply_occupancies(applications, **kwargs)


def _service_with_seats(
    seat_count: int,
) -> tuple[ClassroomService, CountingRepository, str, list[str]]:
    repository = CountingRepository()
    service = ClassroomService(repository, occupancy_confidence_threshold=0.6, clock=lambda: NOW)
    classroom = service.create_classroom(
        code=f"R{seat_count:03d}", name="관측 batch 왕복 시험실", location="4A"
    )
    seat_ids = [
        service.create_seat(
            classroom_id=classroom.id,
            code=f"S{index:02d}",
            label=f"S{index:02d}",
            row=1,
            column=index,
        ).id
        for index in range(1, seat_count + 1)
    ]
    repository.calls.clear()  # 준비 과정의 호출은 세지 않는다
    return service, repository, classroom.id, seat_ids


def _record(
    service: ClassroomService, classroom_id: str, seat_ids: list[str], event_id: str
) -> None:
    service.record_observation_batch(
        RecordSeatObservationBatchCommand(
            event_id=event_id,
            classroom_id=classroom_id,
            source=OccupancySource.SYSTEM,
            observed_at=NOW,
            observations=tuple(
                SeatObservation(seat_id=seat_id, occupied=True, confidence=0.9)
                for seat_id in seat_ids
            ),
        )
    )


@pytest.mark.parametrize("seat_count", [3, 12])
def test_좌석_조회는_좌석_수와_무관하게_한_번이다(seat_count: int) -> None:
    service, repository, classroom_id, seat_ids = _service_with_seats(seat_count)

    _record(service, classroom_id, seat_ids, "event-1")

    # 좌석 검증은 `$in` 한 번으로 끝난다.
    assert repository.calls["get_seats"] == 1
    # 재수신 판정도 event_id 하나로 한 번에 읽는다.
    assert repository.calls["get_histories_by_event"] == 1
    # 좌석마다 따로 묻는 예전 경로는 쓰이지 않아야 한다.
    assert repository.calls["get_history_by_event_and_seat"] == 0


@pytest.mark.parametrize("seat_count", [3, 12])
def test_좌석당_조회가_늘지_않는다(seat_count: int) -> None:
    service, repository, classroom_id, seat_ids = _service_with_seats(seat_count)

    _record(service, classroom_id, seat_ids, "event-1")

    # UoW 미주입 경로에서 좌석당 쓰기는 남는다. 줄인 것은 조회뿐이다.
    assert repository.calls["append_occupancy_history"] == seat_count
    # **좌석 수에 비례해 늘어나는 조회가 없어야 한다.** get_seat은 현재 상태를 다시
    # 맞추는 보정 경로에서만 나오며, 좌석 수의 배수로 늘지 않는다.
    assert repository.calls["get_seats"] == 1
    assert repository.calls["get_histories_by_event"] == 1


def test_같은_event_id를_다시_받아도_이력을_새로_쓰지_않는다() -> None:
    service, repository, classroom_id, seat_ids = _service_with_seats(4)

    _record(service, classroom_id, seat_ids, "event-1")
    writes_after_first = repository.calls["append_occupancy_history"]
    _record(service, classroom_id, seat_ids, "event-1")

    # 배치로 미리 읽은 이력으로 재수신을 판정하므로 멱등성이 그대로 지켜진다.
    assert repository.calls["append_occupancy_history"] == writes_after_first


def _service_with_uow(
    seat_count: int,
) -> tuple[ClassroomService, CountingUnitOfWork, str, list[str]]:
    """UoW를 주입한 서비스. 운영(MongoDB) 조립과 같은 경로를 탄다."""
    repository = InMemoryClassroomRepository()
    uow = CountingUnitOfWork(repository)
    service = ClassroomService(
        repository, uow=uow, occupancy_confidence_threshold=0.6, clock=lambda: NOW
    )
    classroom = service.create_classroom(
        code=f"U{seat_count:03d}", name="UoW 왕복 시험실", location="4A"
    )
    seat_ids = [
        service.create_seat(
            classroom_id=classroom.id,
            code=f"S{index:02d}",
            label=f"S{index:02d}",
            row=1,
            column=index,
        ).id
        for index in range(1, seat_count + 1)
    ]
    uow.calls.clear()
    return service, uow, classroom.id, seat_ids


@pytest.mark.parametrize("seat_count", [3, 12])
def test_좌석_관측을_transaction_한_번으로_적용한다(seat_count: int) -> None:
    service, uow, classroom_id, seat_ids = _service_with_uow(seat_count)

    _record(service, classroom_id, seat_ids, "event-1")

    # **좌석 수와 무관하게 transaction은 한 번이다.** 원격 저장소에서는 transaction
    # 하나가 여러 번 왕복하므로, 좌석 수만큼 곱하면 그것만으로 처리 시간을 지배한다.
    assert uow.calls["batch"] == 1
    assert uow.calls["batch_seats"] == seat_count
    # 좌석마다 따로 여는 예전 경로는 쓰이지 않아야 한다.
    assert uow.calls["single"] == 0


def test_재수신은_이미_있는_이력을_transaction에_담지_않는다() -> None:
    service, uow, classroom_id, seat_ids = _service_with_uow(4)

    _record(service, classroom_id, seat_ids, "event-1")
    uow.calls.clear()
    _record(service, classroom_id, seat_ids, "event-1")

    # 이력이 이미 있으면 쓸 것이 없다. 같은 값을 다시 쓰려고 transaction을 열지 않는다.
    assert uow.calls["batch"] == 0
    assert uow.calls["single"] == 0
