"""좌석 근거를 학생 상태로 바꾸는 순수 규칙.

[결정 0008](../../../docs/architecture/decisions.md)이 정한 판정 규칙이 여기 하나로
모인다. 저장소·HTTP·시계에 의존하지 않는 순수 함수이므로, 판정이 왜 그렇게 나왔는지를
입력만 놓고 재현할 수 있다.

**판정 근거는 좌석 근거(`SeatEvidence`) 하나다.** 좌석 점유와 학생 상태가 같은 값에서
갈라져 나오므로 두 화면이 서로 어긋날 수 없다.

**시간 정책은 이벤트를 받을 때만 적용한다.** 조회는 저장된 결과를 읽기만 하고,
`project_for_display`는 오래된 판정을 화면에서 가릴 뿐 저장된 값을 바꾸지 않는다.

**미관측을 부재로 바꾸지 않는다.** 카메라가 죽었거나 ROI가 없어 좌석을 보지 못한 것과
학생이 자리에 없는 것은 다른 사실이다. `ABSENT`는 좌석이 비어 있는 것을 유예 시간
동안 **계속 본** 경우에만 나온다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from ..classrooms.models import SeatOccupancy
from .models import (
    SeatEvidence,
    StudentState,
    StudentStateReason,
    StudentStateRecord,
)


@dataclass(frozen=True)
class StatePolicy:
    """판정 기준값. 코드에 박지 않고 설정에서 주입한다(결정 0008)."""

    occupancy_confidence_threshold: float
    identity_confidence_threshold: float
    identity_hold_seconds: float
    absent_grace_seconds: float
    stale_seconds: float


@dataclass(frozen=True)
class StudentAssignment:
    """판정에 필요한 좌석 지정 최소 정보."""

    student_id: str
    seat_id: str | None


def decide_student_states(
    *,
    assignments: Sequence[StudentAssignment],
    evidence: Sequence[SeatEvidence],
    unseated: Mapping[str, float],
    previous: Mapping[str, StudentStateRecord],
    classroom_id: str,
    event_id: str,
    observed_at: datetime,
    policy: StatePolicy,
) -> tuple[StudentStateRecord, ...]:
    """이번 프레임의 좌석 근거로 학생별 상태를 정한다.

    `unseated`는 좌석 ROI 밖에서 식별된 학생과 그 신원 신뢰도다.
    `previous`는 직전 판정 결과이며, 신원 유지와 유예 시간 계산의 기준이 된다.

    판정 순서는 근거가 센 것부터다.

    1. 이번 프레임에 좌석에서 식별됐다 → 지정 좌석과 대조해 `PRESENT`/`WRONG_SEAT`
    2. 이번 프레임에 좌석 밖에서 식별됐다 → `IN_CLASSROOM`
    3. 직전 식별이 유지 시간 안이고 그 좌석이 비지 않았다 → 직전 판정을 잇는다
    4. 지정 좌석에 누군가 있으나 미식별 → `UNKNOWN`
    5. 지정 좌석이 비어 있다 → 유예 시간 안이면 `UNKNOWN`, 넘겼으면 `ABSENT`
    6. 지정 좌석을 보지 못했다 → `UNKNOWN`
    """
    seat_state = {item.seat_id: _seat_state(item, policy) for item in evidence}
    identified_seat = _identified_seats(evidence, policy)

    results: list[StudentStateRecord] = []
    for assignment in assignments:
        results.append(
            _decide_one(
                assignment=assignment,
                seat_state=seat_state,
                identified_seat=identified_seat,
                unseated=unseated,
                previous=previous.get(assignment.student_id),
                classroom_id=classroom_id,
                event_id=event_id,
                observed_at=observed_at,
                policy=policy,
            )
        )
    return tuple(results)


@dataclass(frozen=True)
class DisplayState:
    """화면·API가 보여 줄 값. 저장된 판정을 가공만 한 결과다."""

    state: StudentState
    reason: StudentStateReason
    seat_id: str | None
    confidence: float | None


def project_for_display(
    record: StudentStateRecord, now: datetime, policy: StatePolicy
) -> DisplayState:
    """저장된 판정을 화면에 보여 줄 값으로 옮긴다. **저장된 값을 바꾸지 않는다.**

    탐지 이벤트가 한동안 끊기면 마지막 판정이 아무리 확신에 차 있었어도 지금을
    설명하지 못한다. 카메라가 죽은 뒤에도 `PRESENT`를 계속 보여 주는 것이 가장 위험한
    표시이므로, 근거가 오래된 판정은 `UNKNOWN`으로 가린다. 좌석과 신뢰도도 함께
    가린다 — 상태만 모른다고 하면서 자리는 알려 주면 화면이 서로 어긋난다.

    가리기만 하고 `ABSENT`로 바꾸지는 않는다 — 미관측은 부재가 아니다.
    """
    if (now - record.observed_at) > timedelta(seconds=policy.stale_seconds):
        return DisplayState(
            state=StudentState.UNKNOWN,
            reason=StudentStateReason.SEAT_NOT_OBSERVED,
            seat_id=None,
            confidence=None,
        )
    return DisplayState(
        state=record.state,
        reason=record.reason,
        seat_id=record.seat_id,
        confidence=record.confidence,
    )


def _decide_one(
    *,
    assignment: StudentAssignment,
    seat_state: Mapping[str, SeatOccupancy],
    identified_seat: Mapping[str, tuple[str, float]],
    unseated: Mapping[str, float],
    previous: StudentStateRecord | None,
    classroom_id: str,
    event_id: str,
    observed_at: datetime,
    policy: StatePolicy,
) -> StudentStateRecord:
    student_id = assignment.student_id
    assigned_seat_id = assignment.seat_id

    def build(
        state: StudentState,
        reason: StudentStateReason,
        *,
        seat_id: str | None,
        confidence: float | None,
        identified_at: datetime | None,
        vacant_since: datetime | None,
    ) -> StudentStateRecord:
        return StudentStateRecord(
            student_id=student_id,
            classroom_id=classroom_id,
            state=state,
            reason=reason,
            seat_id=seat_id,
            assigned_seat_id=assigned_seat_id,
            confidence=confidence,
            observed_at=observed_at,
            event_id=event_id,
            identified_at=identified_at,
            vacant_since=vacant_since,
        )

    # 1. 이번 프레임에 좌석에서 식별됐다. 가장 센 근거다.
    seated = identified_seat.get(student_id)
    if seated is not None:
        seat_id, confidence = seated
        if assigned_seat_id is None:
            return build(
                StudentState.UNKNOWN,
                StudentStateReason.NO_ASSIGNED_SEAT,
                seat_id=seat_id,
                confidence=confidence,
                identified_at=observed_at,
                vacant_since=None,
            )
        if seat_id == assigned_seat_id:
            return build(
                StudentState.PRESENT,
                StudentStateReason.IDENTIFIED_AT_ASSIGNED_SEAT,
                seat_id=seat_id,
                confidence=confidence,
                identified_at=observed_at,
                vacant_since=None,
            )
        return build(
            StudentState.WRONG_SEAT,
            StudentStateReason.IDENTIFIED_AT_OTHER_SEAT,
            seat_id=seat_id,
            confidence=confidence,
            identified_at=observed_at,
            vacant_since=None,
        )

    # 2. 좌석 밖에서 식별됐다. 누군지는 알지만 어느 자리도 아니다.
    outside = unseated.get(student_id)
    if outside is not None and outside >= policy.identity_confidence_threshold:
        return build(
            StudentState.IN_CLASSROOM,
            StudentStateReason.IDENTIFIED_OUTSIDE_SEATS,
            seat_id=None,
            confidence=outside,
            identified_at=observed_at,
            vacant_since=None,
        )

    # 3. 직전 식별을 유지 시간 안에서 이어받는다. 앉은 사람도 프레임마다 잡히지는
    #    않으므로, 한 프레임 놓쳤다고 상태가 튀면 안 된다. 다만 그 좌석이 비어 있는
    #    것을 실제로 봤다면 붙들지 않는다 — 자리를 뜬 것이 확인된 셈이다.
    held = _held_previous(previous, seat_state, observed_at, policy)
    if held is not None:
        return build(
            held.state,
            StudentStateReason.IDENTITY_HELD,
            seat_id=held.seat_id,
            confidence=held.confidence,
            identified_at=held.identified_at,
            vacant_since=None,
        )

    if assigned_seat_id is None:
        return build(
            StudentState.UNKNOWN,
            StudentStateReason.NO_ASSIGNED_SEAT,
            seat_id=None,
            confidence=None,
            identified_at=_previous_identified_at(previous),
            vacant_since=None,
        )

    # 4~6. 신원이 없다. 지정 좌석의 상태만으로 말할 수 있는 데까지만 말한다.
    assigned_state = seat_state.get(assigned_seat_id)
    if assigned_state is None or assigned_state == SeatOccupancy.UNKNOWN:
        # 이 카메라가 그 좌석을 보지 못했거나, 봤지만 확신하지 못했다.
        return build(
            StudentState.UNKNOWN,
            StudentStateReason.SEAT_NOT_OBSERVED,
            seat_id=None,
            confidence=None,
            identified_at=_previous_identified_at(previous),
            vacant_since=None,
        )
    if assigned_state == SeatOccupancy.OCCUPIED:
        # 누군가 앉아 있지만 누구인지 모른다. 자리가 찼다는 이유로 재석 처리하지 않는다.
        return build(
            StudentState.UNKNOWN,
            StudentStateReason.SEAT_OCCUPIED_BY_UNKNOWN,
            seat_id=None,
            confidence=None,
            identified_at=_previous_identified_at(previous),
            vacant_since=None,
        )

    # 지정 좌석이 비어 있는 것을 실제로 봤다. 여기서부터 유예 시간을 센다.
    vacant_since = _vacant_since(previous, observed_at)
    if (observed_at - vacant_since) >= timedelta(seconds=policy.absent_grace_seconds):
        return build(
            StudentState.ABSENT,
            StudentStateReason.SEAT_VACANT_BEYOND_GRACE,
            seat_id=None,
            confidence=None,
            identified_at=_previous_identified_at(previous),
            vacant_since=vacant_since,
        )
    return build(
        StudentState.UNKNOWN,
        StudentStateReason.SEAT_VACANT_WITHIN_GRACE,
        seat_id=None,
        confidence=None,
        identified_at=_previous_identified_at(previous),
        vacant_since=vacant_since,
    )


def _seat_state(evidence: SeatEvidence, policy: StatePolicy) -> SeatOccupancy:
    """좌석 근거를 좌석 상태로 해석한다. 강의실 서비스와 같은 규칙이다."""
    if not evidence.occupied:
        return SeatOccupancy.VACANT
    if evidence.confidence < policy.occupancy_confidence_threshold:
        return SeatOccupancy.UNKNOWN
    return SeatOccupancy.OCCUPIED


def _identified_seats(
    evidence: Sequence[SeatEvidence], policy: StatePolicy
) -> dict[str, tuple[str, float]]:
    """학생별로 "이번에 식별된 좌석"을 정한다.

    임계값 미만 식별에 이름을 붙이지 않는다. 오인식은 다른 학생의 출결을 바꾸는 사고다.
    같은 학생이 두 좌석에서 잡히면 신원 신뢰도가 높은 쪽만 남긴다.
    """
    result: dict[str, tuple[str, float]] = {}
    for item in evidence:
        if (
            item.student_id is None
            or item.identity_confidence is None
            or item.identity_confidence < policy.identity_confidence_threshold
            or item.confidence < policy.occupancy_confidence_threshold
        ):
            continue
        current = result.get(item.student_id)
        if current is None or item.identity_confidence > current[1]:
            result[item.student_id] = (item.seat_id, item.identity_confidence)
    return result


def _held_previous(
    previous: StudentStateRecord | None,
    seat_state: Mapping[str, SeatOccupancy],
    observed_at: datetime,
    policy: StatePolicy,
) -> StudentStateRecord | None:
    """직전 판정을 그대로 이어도 되는지 본다. 이어도 되면 그 판정을 돌려준다."""
    if previous is None or previous.identified_at is None or previous.seat_id is None:
        return None
    if previous.state not in (StudentState.PRESENT, StudentState.WRONG_SEAT):
        return None
    if (observed_at - previous.identified_at) > timedelta(seconds=policy.identity_hold_seconds):
        return None
    if seat_state.get(previous.seat_id) == SeatOccupancy.VACANT:
        # 그 자리가 비어 있는 것을 실제로 봤다. 붙들 근거가 사라졌다.
        return None
    return previous


def _previous_identified_at(previous: StudentStateRecord | None) -> datetime | None:
    return None if previous is None else previous.identified_at


def _vacant_since(previous: StudentStateRecord | None, observed_at: datetime) -> datetime:
    """지정 좌석이 비어 보이기 시작한 시각. 직전에 이미 세고 있었으면 그것을 잇는다."""
    if previous is not None and previous.vacant_since is not None:
        return previous.vacant_since
    return observed_at
