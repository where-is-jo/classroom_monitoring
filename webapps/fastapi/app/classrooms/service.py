"""인증·알림·감사와 분리된 강의실 좌석 서비스."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from math import isfinite
from uuid import NAMESPACE_URL, uuid4, uuid5

from ..shared.errors import StudentNotFoundError
from ..shared.student_identity import StudentLookupPort
from .errors import (
    ClassroomConcurrentUpdateError,
    ClassroomDuplicateError,
    ClassroomInputError,
    ClassroomNotFoundError,
    SeatBatchConflictError,
    SeatCodeAllocationConflictError,
    SeatCodeConflictError,
    SeatCoordinateConflictError,
    SeatDuplicateError,
    SeatInactiveForAssignmentError,
    SeatNotFoundError,
    StudentInactiveForAssignmentError,
)
from .models import (
    Classroom,
    ClassroomOccupancySummary,
    ClassroomPage,
    CreateClassroomCommand,
    CreateSeatCommand,
    ObservationBatchStatus,
    OccupancySource,
    RecordSeatObservationBatchCommand,
    Seat,
    SeatAssignment,
    SeatAssignmentInfo,
    SeatCurrentOccupancy,
    SeatGeometry,
    SeatObservation,
    SeatObservationBatchRecord,
    SeatObservationBatchResult,
    SeatOccupancy,
    SeatOccupancyHistory,
    SeatPage,
)
from .ports import ClassroomRepository, SeatAssignmentRepository, SeatMutationUnitOfWork

# allocator가 인식하는 좌석 코드 패턴: S 뒤에 양의 정수 1개 이상.
_SEAT_CODE_PATTERN = re.compile(r"^S(\d+)$")

# batch 좌석 사전 검증과 처리 중 hard delete 경쟁에서 공통으로 쓰는 422 메시지.
_OBSERVATION_SEAT_REQUIREMENT = "batch의 모든 좌석은 요청한 강의실 소속의 활성 좌석이어야 합니다."

# 좌석 current 반영(CAS) 재시도 상한. 소진 시 ClassroomConcurrentUpdateError.
_OCCUPANCY_RETRY_LIMIT = 3


class ClassroomService:
    def __init__(
        self,
        repository: ClassroomRepository,
        *,
        student_lookup: StudentLookupPort | None = None,  # 학생 조회·활성 검증용 (중립 계약)
        assignment_repository: SeatAssignmentRepository | None = None,  # 좌석-학생 지정 관리용
        uow: SeatMutationUnitOfWork
        | None = None,  # 지정 mutation 단위 작업 (주입 시 직접 mutation 금지)
        occupancy_confidence_threshold: float,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._student_lookup = student_lookup
        self._assignment_repository = assignment_repository
        self._uow = uow
        self._threshold = occupancy_confidence_threshold
        self._clock = clock

    def create_seat(
        self,
        classroom_id: str,
        code: str,
        label: str,
        row: int | None = None,
        column: int | None = None,
        geometry: SeatGeometry | None = None,
    ) -> Seat:
        classroom = self._required_active_classroom(classroom_id)
        normalized_code = self._code(code)
        normalized_label = self._text(label, "좌석 이름")
        self._validate_row_column(row, column)
        normalized_geometry = self._geometry(geometry)
        now = self._aware_datetime(self._clock())
        return self._repository.create_seat(
            Seat(
                id=str(uuid4()),
                classroom_id=classroom.id,
                code=normalized_code,
                label=normalized_label,
                row=row,
                column=column,
                geometry=normalized_geometry,
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

    def create_seat_auto(self, classroom_id: str, *, row: int, column: int) -> Seat:
        """좌석을 자동 코드로 생성한다. label은 ``좌석 {code}``로 설정한다.

        - allocator가 현재 seat 문서(legacy inactive 포함)를 읽어 최소 누락 코드를 만든다.
        - code conflict(동시 할당 레이스)에서만 할당·삽입을 최대 3회 재시도하고,
          재시도 소진 시 ``SeatCodeAllocationConflictError``를 던진다.
        - 좌표 conflict는 재시도하지 않고 즉시 ``SeatCoordinateConflictError``를 던진다.
        """
        classroom = self._required_active_classroom(classroom_id)
        self._validate_row_column(row, column)
        last_code_conflict: SeatCodeConflictError | None = None
        for _ in range(3):
            try:
                return self._create_auto_seat(classroom.id, row=row, column=column)
            except SeatCodeConflictError as exc:
                last_code_conflict = exc
        raise SeatCodeAllocationConflictError() from last_code_conflict

    def _create_auto_seat(self, classroom_id: str, *, row: int, column: int) -> Seat:
        code = self._allocate_seat_code(classroom_id)
        now = self._aware_datetime(self._clock())
        seat = Seat(
            id=str(uuid4()),
            classroom_id=classroom_id,
            code=code,
            label=f"좌석 {code}",
            row=row,
            column=column,
            geometry=None,
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
        try:
            return self._repository.create_seat(seat)
        except SeatDuplicateError:
            if self._is_coordinate_conflict(classroom_id, row=row, column=column):
                raise SeatCoordinateConflictError() from None
            raise SeatCodeConflictError() from None

    def _allocate_seat_code(self, classroom_id: str) -> str:
        """``^S(\\d+)$`` 코드 중 최소 누락값으로 새 코드를 만든다.

        현재 seat 문서(비활성 포함)가 예약한 양의 정수를 파싱해,
        S01~S99는 2자리 0-padding, S100 이후는 자연수 표기로 생성한다.
        """
        seats = self._repository.list_all_seats_for_allocation(classroom_id)
        reserved: set[int] = set()
        for seat in seats:
            match = _SEAT_CODE_PATTERN.fullmatch(seat.code)
            if match is not None:
                reserved.add(int(match.group(1)))
        candidate = 1
        while candidate in reserved:
            candidate += 1
        if candidate < 100:
            return f"S{candidate:02d}"
        return f"S{candidate}"

    def _is_coordinate_conflict(self, classroom_id: str, *, row: int, column: int) -> bool:
        """현재 seat 문서(비활성 포함) 중 (row, column)을 예약한 좌석이 있는지 확인한다."""
        return any(
            seat.row == row and seat.column == column
            for seat in self._repository.list_all_seats_for_allocation(classroom_id)
        )

    def seed_classroom(self, command: CreateClassroomCommand) -> Classroom:
        classroom_id = self._identifier(command.id, "강의실 ID")
        code = self._code(command.code)
        name = self._text(command.name, "강의실 이름")
        location = self._text(command.location, "강의실 위치")
        existing = self._repository.get_classroom(classroom_id)
        if existing is not None:
            if (existing.code, existing.name, existing.location) != (code, name, location):
                raise ClassroomDuplicateError()
            return existing
        return self._repository.create_classroom(
            Classroom(
                id=classroom_id,
                code=code,
                name=name,
                location=location,
                is_active=True,
                created_at=self._aware_datetime(self._clock()),
            )
        )

    def create_classroom(self, code: str, name: str, location: str) -> Classroom:
        normalized_code = self._code(code)
        normalized_name = self._text(name, "강의실 이름")
        normalized_location = self._text(location, "강의실 위치")
        if self._repository.get_classroom_by_code(normalized_code) is not None:
            raise ClassroomDuplicateError()
        return self._repository.create_classroom(
            Classroom(
                id=str(uuid4()),
                code=normalized_code,
                name=normalized_name,
                location=normalized_location,
                is_active=True,
                created_at=self._aware_datetime(self._clock()),
            )
        )

    def seed_seat(self, command: CreateSeatCommand) -> Seat:
        classroom = self._required_active_classroom(command.classroom_id)
        seat_id = self._identifier(command.id, "좌석 ID")
        code = self._code(command.code)
        label = self._text(command.label, "좌석 이름")
        row = command.row
        column = command.column
        self._validate_row_column(row, column)
        geometry = self._geometry(command.geometry)
        existing = self._repository.get_seat(seat_id)
        if existing is not None:
            if (
                existing.classroom_id,
                existing.code,
                existing.label,
                existing.row,
                existing.column,
                existing.geometry,
            ) != (classroom.id, code, label, row, column, geometry):
                raise SeatDuplicateError()
            return existing
        now = self._aware_datetime(self._clock())
        return self._repository.create_seat(
            Seat(
                id=seat_id,
                classroom_id=classroom.id,
                code=code,
                label=label,
                row=row,
                column=column,
                geometry=geometry,
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

    def list_classrooms(self, *, limit: int, offset: int) -> ClassroomPage:
        return self._repository.list_classrooms(limit=limit, offset=offset)

    def get_classroom(self, classroom_id: str) -> Classroom:
        return self._required_active_classroom(classroom_id)

    def update_classroom(
        self,
        classroom_id: str,
        code: str | None = None,
        name: str | None = None,
        location: str | None = None,
        is_active: bool | None = None,
    ) -> Classroom:
        classroom = self._required_active_classroom(classroom_id)
        updated_code = self._code(classroom.code if code is None else code)
        updated_name = self._text(classroom.name if name is None else name, "강의실 이름")
        updated_location = self._text(
            classroom.location if location is None else location, "강의실 위치"
        )
        existing = self._repository.get_classroom_by_code(updated_code)
        if existing is not None and existing.id != classroom.id:
            raise ClassroomDuplicateError()
        return self._repository.update_classroom(
            replace(
                classroom,
                code=updated_code,
                name=updated_name,
                location=updated_location,
                is_active=classroom.is_active if is_active is None else is_active,
            )
        )

    def delete_classroom(self, classroom_id: str) -> None:
        classroom = self._required_active_classroom(classroom_id)
        self._repository.delete_classroom(classroom.id)

    def list_seats(self, classroom_id: str, *, limit: int, offset: int) -> SeatPage:
        classroom = self._required_active_classroom(classroom_id)
        return self._repository.list_seats(classroom.id, limit=limit, offset=offset)

    def list_all_seats(self, classroom_id: str) -> list[Seat]:
        """강의실의 활성 좌석 전체를 한 번에 돌려준다. 매핑처럼 전체가 필요한 곳에서 쓴다."""
        classroom = self._required_active_classroom(classroom_id)
        return self._all_seats(classroom.id)

    def get_seat(self, seat_id: str) -> Seat:
        seat = self._required_seat(seat_id)
        if not seat.is_active:
            raise SeatNotFoundError()
        return seat

    def update_seat(
        self,
        seat_id: str,
        code: str | None = None,
        label: str | None = None,
        row: int | None = None,
        column: int | None = None,
        geometry: SeatGeometry | None = None,
        is_active: bool | None = None,
        *,
        unset_row: bool = False,
        unset_column: bool = False,
    ) -> Seat:
        seat = self._required_seat(seat_id)
        if not seat.is_active:
            raise SeatNotFoundError()

        # row, column 검증
        self._validate_row_column(row, column)

        # row, column 처리 (unset_row/unset_column이 우선한다)
        if unset_row:
            updated_row = None
        elif row is not None:
            updated_row = row
        else:
            updated_row = seat.row

        if unset_column:
            updated_column = None
        elif column is not None:
            updated_column = column
        else:
            updated_column = seat.column

        # 최종 상태 검증 (부분 해제 방지)
        self._validate_row_column(updated_row, updated_column)

        updated_code = self._code(seat.code if code is None else code)
        updated_label = self._text(seat.label if label is None else label, "좌석 이름")
        updated_geometry = seat.geometry if geometry is None else self._geometry(geometry)

        # row/column 해제 시 $unset 대상 필드 전달
        unset_fields: list[str] = []
        if unset_row:
            unset_fields.append("row")
        if unset_column:
            unset_fields.append("column")

        return self._repository.update_seat(
            replace(
                seat,
                code=updated_code,
                label=updated_label,
                row=updated_row,
                column=updated_column,
                geometry=updated_geometry,
                is_active=seat.is_active if is_active is None else is_active,
                updated_at=self._aware_datetime(self._clock()),
            ),
            unset_fields=unset_fields if unset_fields else None,
        )

    def delete_seat(self, seat_id: str) -> None:
        seat = self._required_seat(seat_id)
        if not seat.is_active:
            raise SeatNotFoundError()
        if self._uow is not None:
            self._uow.delete_seat_and_unassign(seat.id)
        else:
            # UoW 미주입(레거시 경로): 삭제 전 지정 해제 (베스트 에포트)
            if self._assignment_repository is not None:
                self._assignment_repository.unassign(seat.id)
            self._repository.delete_seat(seat.id)

    # ============================================================
    # 좌석-학생 지정
    # ============================================================

    def assign_student(
        self,
        seat_id: str,
        student_id: str,
    ) -> SeatAssignmentInfo:
        """좌석에 학생을 지정한다.

        - 같은 학생을 같은 좌석에 다시 지정하면 멱등하게 동작한다.
        - 이미 같은 강의실의 다른 좌석에 지정된 학생이면 기존 지정을 해제하고 새 좌석에 지정한다.
        - 비활성화된 학생/좌석이면 예외를 발생시킨다.
        """
        if self._assignment_repository is None and self._uow is None:
            raise ClassroomInputError("지정 저장소가 연결되지 않았습니다.")

        seat = self._required_seat(seat_id)
        if not seat.is_active:
            raise SeatInactiveForAssignmentError()

        if self._student_lookup is not None:
            student = self._student_lookup.find_by_id(student_id)
            if student is None:
                raise StudentNotFoundError()
            if not student.is_active:
                raise StudentInactiveForAssignmentError()

        if self._uow is not None:
            # UoW가 기존 지정 해제 + 새 지정을 원자적으로 수행한다.
            saved = self._uow.assign_or_move_if_active(seat_id, student_id, seat.classroom_id)
        else:
            assert self._assignment_repository is not None
            # 같은 강의실 내에서 이미 지정된 학생이 있으면 기존 지정 해제 (이동)
            existing = self._assignment_repository.get_by_student(student_id, seat.classroom_id)
            if existing is not None and existing.seat_id != seat_id:
                self._assignment_repository.unassign(existing.seat_id)

            now = self._aware_datetime(self._clock())
            assignment = SeatAssignment(
                seat_id=seat_id,
                student_id=student_id,
                classroom_id=seat.classroom_id,
                assigned_at=now,
            )
            saved = self._assignment_repository.assign(assignment)

        # SeatAssignmentInfo 조회 (학생 이름/학번 보강)
        student_name = ""
        student_no = ""
        if self._student_lookup is not None:
            student = self._student_lookup.find_by_id(student_id)
            if student is not None:
                student_name = student.name
                student_no = student.student_no

        return SeatAssignmentInfo(
            seat_id=saved.seat_id,
            seat_label=seat.label,
            student_id=saved.student_id,
            student_name=student_name,
            student_no=student_no,
            assigned_at=saved.assigned_at,
        )

    def unassign_student(self, seat_id: str) -> None:
        """좌석-학생 지정을 해제한다."""
        if self._uow is not None:
            self._uow.unassign_if_active(seat_id)
            return
        if self._assignment_repository is None:
            raise ClassroomInputError("지정 저장소가 연결되지 않았습니다.")
        self._assignment_repository.unassign(seat_id)

    def unassign_by_student(self, student_id: str) -> int:
        """학생의 모든 좌석 지정을 해제한다. 해제된 지정 수를 반환한다."""
        if self._assignment_repository is None:
            raise ClassroomInputError("지정 저장소가 연결되지 않았습니다.")
        return self._assignment_repository.unassign_by_student(student_id)

    def list_assignments(self, classroom_id: str) -> list[SeatAssignmentInfo]:
        """강의실의 전체 좌석-학생 지정 현황을 반환한다."""
        if self._assignment_repository is None:
            return []

        assignments = self._assignment_repository.list_by_classroom(classroom_id)
        result: list[SeatAssignmentInfo] = []
        for assignment in assignments:
            student_name = ""
            student_no = ""
            if self._student_lookup is not None:
                student = self._student_lookup.find_by_id(assignment.student_id)
                # missing/unknown·inactive는 historical blank name으로 판단한다.
                if student is not None and student.is_active:
                    student_name = student.name
                    student_no = student.student_no

            seat = self._repository.get_seat(assignment.seat_id)
            seat_label = seat.label if seat else ""

            result.append(
                SeatAssignmentInfo(
                    seat_id=assignment.seat_id,
                    seat_label=seat_label,
                    student_id=assignment.student_id,
                    student_name=student_name,
                    student_no=student_no,
                    assigned_at=assignment.assigned_at,
                )
            )
        return result

    def list_assignments_raw(self, classroom_id: str) -> list[SeatAssignment]:
        """강의실의 전체 좌석-학생 지정을 도메인 모델 그대로 돌려준다.

        list_assignments는 학생 이름·학번을 보강한 조회용 객체를 만드는 반면,
        학생 상태 판정처럼 원시 지정(seat_id·student_id)만 필요한 호출자는
        이 메서드를 사용한다.
        """
        if self._assignment_repository is None:
            return []
        return self._assignment_repository.list_by_classroom(classroom_id)

    def occupancy_summary(self, classroom_id: str) -> ClassroomOccupancySummary:
        classroom = self._required_active_classroom(classroom_id)
        seats = self._all_seats(classroom.id)
        states = [seat.current_occupancy.state for seat in seats]
        observed = [
            seat.current_occupancy.observed_at
            for seat in seats
            if seat.current_occupancy.observed_at is not None
        ]
        return ClassroomOccupancySummary(
            classroom=classroom,
            seats=seats,
            total=len(seats),
            occupied_count=states.count(SeatOccupancy.OCCUPIED),
            vacant_count=states.count(SeatOccupancy.VACANT),
            unknown_count=states.count(SeatOccupancy.UNKNOWN),
            last_observed_at=max(observed) if observed else None,
        )

    def _all_seats(self, classroom_id: str) -> list[Seat]:
        seats: list[Seat] = []
        while True:
            page = self._repository.list_seats(classroom_id, limit=200, offset=len(seats))
            seats.extend(page.items)
            if not page.items or len(seats) >= page.total:
                return seats

    def record_seat_observation_batch(
        self,
        *,
        event_id: str,
        classroom_id: str,
        observations: tuple[SeatObservation, ...],
        observed_at: datetime,
    ) -> SeatObservationBatchResult:
        """탐지 이벤트를 근거로 좌석 점유 batch를 기록한다. source는 항상 SYSTEM이다."""
        return self.record_observation_batch(
            RecordSeatObservationBatchCommand(
                event_id=event_id,
                classroom_id=classroom_id,
                source=OccupancySource.SYSTEM,
                observed_at=observed_at,
                observations=observations,
            )
        )

    def record_observation_batch(
        self, command: RecordSeatObservationBatchCommand
    ) -> SeatObservationBatchResult:
        classroom = self._required_active_classroom(command.classroom_id)
        event_id = self._identifier(command.event_id, "event ID")
        observed_at = self._aware_datetime(command.observed_at)
        observations = self._observations(command.observations)
        existing = self._repository.get_observation_batch(event_id)
        if existing is not None:
            if not self._same_batch(existing, command, observed_at=observed_at):
                raise SeatBatchConflictError()
            if existing.status == ObservationBatchStatus.COMPLETED:
                return self._batch_result(existing)

        # **좌석마다 따로 묻지 않는다.** 이 저장소는 원격 Atlas라 왕복 1회가 약 42ms고,
        # 한 이벤트가 좌석 7개를 관측하면 그것만으로 0.3초가 든다. 조회 한 번으로 모아
        # 읽고 검증은 그대로 좌석마다 한다.
        found = self._repository.get_seats([item.seat_id for item in observations])
        seats: dict[str, Seat] = {}
        for observation in observations:
            seat = found.get(observation.seat_id)
            if seat is None or not seat.is_active or seat.classroom_id != classroom.id:
                raise ClassroomInputError(_OBSERVATION_SEAT_REQUIREMENT)
            seats[seat.id] = seat

        claimed = existing
        if claimed is None:
            received_at = self._aware_datetime(self._clock())
            claimed = self._repository.claim_observation_batch(
                SeatObservationBatchRecord(
                    event_id=event_id,
                    classroom_id=classroom.id,
                    source=command.source,
                    observed_at=observed_at,
                    observations=observations,
                    status=ObservationBatchStatus.PROCESSING,
                    processed_count=0,
                    changed_count=0,
                    received_at=received_at,
                    completed_at=None,
                )
            )
        if claimed.status == ObservationBatchStatus.COMPLETED:
            return self._batch_result(claimed)

        # 재수신 판정도 좌석마다 묻지 않는다. 같은 event_id의 이력은 관측 좌석 수를
        # 넘지 않으므로 한 번에 읽어 두고 메모리에서 찾는다.
        known_histories = self._repository.get_histories_by_event(event_id)
        try:
            histories = [
                self._apply_observation(
                    observation,
                    classroom_id=classroom.id,
                    event_id=event_id,
                    source=command.source,
                    observed_at=observed_at,
                    received_at=claimed.received_at,
                    known_histories=known_histories,
                    known_seat=seats.get(observation.seat_id),
                )
                for observation in observations
            ]
        except SeatNotFoundError as exc:
            # 사전 검증을 통과한 직후 좌석이 hard delete되는 경쟁(claim 이후 delete-first).
            # UoW가 좌석 부재를 확인하고 history·current를 하나도 쓰지 않으므로,
            # 사전 검증과 같은 422로 수렴시켜 두 저장소에서 동일한 결과를 낸다.
            # batch는 COMPLETED로 넘어가지 않고 PROCESSING으로 남아, 같은 event_id의
            # 재요청도 사전 검증에서 다시 422가 된다 (재시도 결과가 멱등하다).
            raise ClassroomInputError(_OBSERVATION_SEAT_REQUIREMENT) from exc
        completed = replace(
            claimed,
            status=ObservationBatchStatus.COMPLETED,
            processed_count=len(histories),
            changed_count=sum(item.state_changed for item in histories),
            completed_at=self._aware_datetime(self._clock()),
        )
        return self._batch_result(self._repository.complete_observation_batch(completed))

    def _apply_observation(
        self,
        observation: SeatObservation,
        *,
        classroom_id: str,
        event_id: str,
        source: OccupancySource,
        observed_at: datetime,
        received_at: datetime,
        known_histories: Mapping[str, SeatOccupancyHistory] | None = None,
        known_seat: Seat | None = None,
    ) -> SeatOccupancyHistory:
        # **매핑 자체를 받는다.** 값 하나만 받으면 `None`이 "이력 없음"인지 "미제공"인지
        # 구분되지 않아, 이미 batch로 확인한 좌석까지 다시 묻게 된다.
        if known_histories is not None:
            existing = known_histories.get(observation.seat_id)
        else:
            existing = self._repository.get_history_by_event_and_seat(event_id, observation.seat_id)
        if existing is not None:
            self._repair_current_from_history(existing)
            return existing
        if self._uow is None:
            # UoW 미주입(레거시 경로): history append와 current 반영이 분리된다.
            seat = known_seat or self._required_seat(observation.seat_id)
            history = self._build_history(
                seat,
                observation,
                classroom_id=classroom_id,
                event_id=event_id,
                source=source,
                observed_at=observed_at,
                received_at=received_at,
            )
            stored = self._repository.append_occupancy_history(history)
            self._repair_current_from_history(stored)
            return stored
        # UoW 주입 경로: history 기록과 current 반영을 hard delete와 같은
        # session/lock에서 원자적으로 수행한다. 좌석이 사라졌으면 아무것도 쓰지
        # 않고 SeatNotFoundError가 올라오고, version이 어긋나면 최신 좌석으로
        # history를 다시 만들어 재시도한다.
        for attempt in range(_OCCUPANCY_RETRY_LIMIT):
            # **첫 시도만 미리 읽어 둔 좌석을 쓴다.** 재시도는 version이 어긋났다는
            # 뜻이므로 반드시 최신 좌석을 다시 읽어야 한다 — 그러지 않으면 같은
            # version으로 무한히 실패한다.
            seat = (
                known_seat
                if attempt == 0 and known_seat is not None
                else self._required_seat(observation.seat_id)
            )
            history = self._build_history(
                seat,
                observation,
                classroom_id=classroom_id,
                event_id=event_id,
                source=source,
                observed_at=observed_at,
                received_at=received_at,
            )
            applied = self._uow.append_history_and_apply_occupancy(
                history,
                expected_version=seat.version,
                occupancy=self._occupancy_update(seat, history),
                updated_at=self._aware_datetime(self._clock()),
            )
            if applied is not None:
                return applied
        raise ClassroomConcurrentUpdateError()

    def _build_history(
        self,
        seat: Seat,
        observation: SeatObservation,
        *,
        classroom_id: str,
        event_id: str,
        source: OccupancySource,
        observed_at: datetime,
        received_at: datetime,
    ) -> SeatOccupancyHistory:
        """좌석의 현재 상태를 기준으로 관측 history를 만든다."""
        current = seat.current_occupancy
        target = self._occupancy(observation)
        applied = current.observed_at is None or observed_at >= current.observed_at
        return SeatOccupancyHistory(
            id=str(uuid5(NAMESPACE_URL, f"seat-occupancy-history:{event_id}:{seat.id}")),
            seat_id=seat.id,
            classroom_id=classroom_id,
            event_id=event_id,
            source=source,
            from_state=current.state,
            to_state=target,
            occupied=observation.occupied,
            confidence=observation.confidence,
            observed_at=observed_at,
            received_at=received_at,
            applied_to_current=applied,
            state_changed=applied and current.state != target,
        )

    @staticmethod
    def _occupancy_update(seat: Seat, history: SeatOccupancyHistory) -> SeatCurrentOccupancy | None:
        """history를 좌석 current에 반영해야 하면 새 current를, 아니면 None을 준다."""
        if not history.applied_to_current:
            return None
        current = seat.current_occupancy
        if current.event_id == history.event_id:
            return None
        if current.observed_at is not None and current.observed_at > history.observed_at:
            return None
        return SeatCurrentOccupancy(
            state=history.to_state,
            source=history.source,
            confidence=history.confidence,
            observed_at=history.observed_at,
            event_id=history.event_id,
        )

    def _repair_current_from_history(self, history: SeatOccupancyHistory) -> None:
        if not history.applied_to_current:
            return
        for _ in range(_OCCUPANCY_RETRY_LIMIT):
            current = self._required_seat(history.seat_id)
            occupancy = self._occupancy_update(current, history)
            if occupancy is None:
                return
            now = self._aware_datetime(self._clock())
            if self._uow is not None:
                # 이미 저장된 history를 그대로 넘겨 current만 원자적으로 반영한다.
                if (
                    self._uow.append_history_and_apply_occupancy(
                        history,
                        expected_version=current.version,
                        occupancy=occupancy,
                        updated_at=now,
                    )
                    is not None
                ):
                    return
                continue
            updated = replace(
                current,
                current_occupancy=occupancy,
                updated_at=now,
                version=current.version + 1,
            )
            if self._repository.replace_seat(updated, expected_version=current.version) is not None:
                return
        raise ClassroomConcurrentUpdateError()

    def _required_active_classroom(self, classroom_id: str) -> Classroom:
        classroom = self._repository.get_classroom(classroom_id)
        if classroom is None or not classroom.is_active:
            raise ClassroomNotFoundError()
        return classroom

    def _required_seat(self, seat_id: str) -> Seat:
        seat = self._repository.get_seat(seat_id)
        if seat is None:
            raise SeatNotFoundError()
        return seat

    def _occupancy(self, observation: SeatObservation) -> SeatOccupancy:
        """관측을 좌석 상태로 해석한다.

        신뢰도 임계값은 **점유 근거에만** 적용한다. 사람을 봤다고 하는데 확신이 없으면
        `UNKNOWN`이지만, 비어 있다는 관측에는 임계값을 적용할 대상이 없다.

        예전에는 임계값을 양쪽에 적용해 `occupied=False`(신뢰도 0.0)가 항상 `UNKNOWN`이
        됐다. 그래서 `VACANT`가 한 번도 만들어지지 않았고, 화면에서 "빈 자리"와
        "관측하지 못한 자리"가 구분되지 않았다. 관측 범위는 이미 ROI가 등록된 좌석으로
        제한돼 있으므로(결정 0020), 여기서 온 `occupied=False`는 "그 카메라가 보는
        자리인데 아무도 없다"는 뜻이다. 그것이 `VACANT`다.
        """
        if not observation.occupied:
            return SeatOccupancy.VACANT
        if observation.confidence < self._threshold:
            return SeatOccupancy.UNKNOWN
        return SeatOccupancy.OCCUPIED

    @staticmethod
    def _observations(values: tuple[SeatObservation, ...]) -> tuple[SeatObservation, ...]:
        if not values or len(values) > 200:
            raise ClassroomInputError("좌석 관측은 1~200개여야 합니다.")
        seat_ids: set[str] = set()
        for value in values:
            if value.seat_id in seat_ids:
                raise ClassroomInputError("한 batch에 같은 좌석을 두 번 넣을 수 없습니다.")
            seat_ids.add(value.seat_id)
            if not isfinite(value.confidence) or not 0 <= value.confidence <= 1:
                raise ClassroomInputError("confidence는 0~1의 유한한 값이어야 합니다.")
        return values

    @staticmethod
    def _same_batch(
        record: SeatObservationBatchRecord,
        command: RecordSeatObservationBatchCommand,
        *,
        observed_at: datetime,
    ) -> bool:
        return (
            record.classroom_id == command.classroom_id
            and record.source == command.source
            and record.observed_at == observed_at
            and record.observations == command.observations
        )

    @staticmethod
    def _batch_result(record: SeatObservationBatchRecord) -> SeatObservationBatchResult:
        return SeatObservationBatchResult(
            event_id=record.event_id,
            processed_count=record.processed_count,
            changed_count=record.changed_count,
        )

    @staticmethod
    def _identifier(value: str, label: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 200:
            raise ClassroomInputError(f"{label}가 올바르지 않습니다.")
        return normalized

    @staticmethod
    def _text(value: str, label: str) -> str:
        normalized = " ".join(value.split())
        if not normalized or len(normalized) > 200:
            raise ClassroomInputError(f"{label}은 1~200자여야 합니다.")
        return normalized

    @staticmethod
    def _code(value: str) -> str:
        normalized = value.strip().upper()
        if not normalized or len(normalized) > 64:
            raise ClassroomInputError("코드는 1~64자여야 합니다.")
        return normalized

    @staticmethod
    def _validate_row_column(row: int | None, column: int | None) -> None:
        """행·열 검증 로직.

        - row와 column은 1 이상의 정수여야 한다.
        - 둘 다 입력하거나 둘 다 비워야 한다 (부분 입력 불가).
        """
        if row is not None and row < 1:
            raise ClassroomInputError("행은 1 이상이어야 합니다.")
        if column is not None and column < 1:
            raise ClassroomInputError("열은 1 이상이어야 합니다.")
        if (row is None) != (column is None):
            raise ClassroomInputError("행과 열은 모두 입력하거나 모두 비워야 합니다.")

    @staticmethod
    def _geometry(value: SeatGeometry | None) -> SeatGeometry | None:
        if value is None:
            return None
        coordinates = (value.x, value.y, value.width, value.height)
        if any(not isfinite(item) for item in coordinates):
            raise ClassroomInputError("좌석 위치는 유한한 값이어야 합니다.")
        if (
            value.x < 0
            or value.y < 0
            or value.width <= 0
            or value.height <= 0
            or value.x + value.width > 1
            or value.y + value.height > 1
        ):
            raise ClassroomInputError("좌석 위치는 0~1 정규화 영역 안에 있어야 합니다.")
        return value

    @staticmethod
    def _aware_datetime(value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ClassroomInputError("시각은 timezone을 포함해야 합니다.")
        return value.astimezone(UTC)
