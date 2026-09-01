"""좌석 근거를 학생 상태로 바꾸는 순수 규칙 테스트.

저장소도 HTTP도 시계도 없이 입력만으로 판정을 재현한다. 판정 규칙이 순수 함수로
분리돼 있어야 "왜 이 상태가 나왔는가"를 되짚을 수 있다(결정 0008).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.student_monitoring.models import (
    SeatEvidence,
    StudentState,
    StudentStateReason,
    StudentStateRecord,
)
from app.student_monitoring.state_rules import (
    StatePolicy,
    StudentAssignment,
    decide_student_states,
    project_for_display,
)

NOW = datetime(2026, 8, 21, 9, 0, tzinfo=UTC)
CLASSROOM = "classroom-a"
STUDENT = "student-1"
POLICY = StatePolicy(
    occupancy_confidence_threshold=0.6,
    identity_confidence_threshold=0.5,
    identity_hold_seconds=15,
    absent_grace_seconds=300,
    stale_seconds=600,
)


def _evidence(
    seat_id: str,
    *,
    occupied: bool = True,
    confidence: float = 0.9,
    student_id: str | None = None,
    identity_confidence: float | None = None,
) -> SeatEvidence:
    return SeatEvidence(
        seat_id=seat_id,
        occupied=occupied,
        confidence=confidence,
        student_id=student_id,
        identity_confidence=identity_confidence,
    )


def _decide(
    evidence: tuple[SeatEvidence, ...],
    *,
    assigned_seat: str | None = "seat-1",
    unseated: dict[str, float] | None = None,
    previous: StudentStateRecord | None = None,
    observed_at: datetime = NOW,
    policy: StatePolicy = POLICY,
) -> StudentStateRecord:
    decided = decide_student_states(
        assignments=[StudentAssignment(student_id=STUDENT, seat_id=assigned_seat)],
        evidence=evidence,
        unseated=unseated or {},
        previous={} if previous is None else {STUDENT: previous},
        classroom_id=CLASSROOM,
        event_id="event-1",
        observed_at=observed_at,
        policy=policy,
    )
    return decided[0]


def test_지정_좌석에서_식별되면_재석이다() -> None:
    record = _decide(
        (_evidence("seat-1", student_id=STUDENT, identity_confidence=0.8),),
    )

    assert record.state == StudentState.PRESENT
    assert record.reason == StudentStateReason.IDENTIFIED_AT_ASSIGNED_SEAT
    assert record.seat_id == "seat-1"
    assert record.confidence == 0.8
    assert record.identified_at == NOW


def test_다른_좌석에서_식별되면_잘못된_자리다() -> None:
    record = _decide(
        (
            _evidence("seat-1", occupied=False, confidence=0.0),
            _evidence("seat-2", student_id=STUDENT, identity_confidence=0.8),
        ),
    )

    assert record.state == StudentState.WRONG_SEAT
    assert record.reason == StudentStateReason.IDENTIFIED_AT_OTHER_SEAT
    assert record.seat_id == "seat-2"


def test_좌석_밖에서_식별되면_강의실_안이다() -> None:
    """누군지 아는 사람이 어느 자리에도 없는 상태다(결정 0025의 7번)."""
    record = _decide(
        (_evidence("seat-1", occupied=False, confidence=0.0),),
        unseated={STUDENT: 0.7},
    )

    assert record.state == StudentState.IN_CLASSROOM
    assert record.reason == StudentStateReason.IDENTIFIED_OUTSIDE_SEATS
    assert record.seat_id is None


def test_임계값_미만_식별에는_이름을_붙이지_않는다() -> None:
    """오인식은 다른 학생의 출결을 바꾸는 사고다."""
    record = _decide(
        (_evidence("seat-1", student_id=STUDENT, identity_confidence=0.49),),
    )

    assert record.state == StudentState.UNKNOWN
    assert record.reason == StudentStateReason.SEAT_OCCUPIED_BY_UNKNOWN


def test_지정_좌석에_모르는_사람이_있으면_재석으로_보지_않는다() -> None:
    record = _decide((_evidence("seat-1"),))

    assert record.state == StudentState.UNKNOWN
    assert record.reason == StudentStateReason.SEAT_OCCUPIED_BY_UNKNOWN
    assert record.seat_id is None


def test_한_프레임_놓쳐도_유지_시간_안이면_직전_판정을_잇는다() -> None:
    """앉은 사람도 프레임마다 잡히지 않는다. 한 번 놓쳤다고 상태가 튀면 안 된다."""
    previous = _decide((_evidence("seat-1", student_id=STUDENT, identity_confidence=0.8),))

    record = _decide(
        (_evidence("seat-1"),),  # 사람은 있는데 이번엔 신원이 안 붙었다
        previous=previous,
        observed_at=NOW + timedelta(seconds=5),
    )

    assert record.state == StudentState.PRESENT
    assert record.reason == StudentStateReason.IDENTITY_HELD
    assert record.identified_at == NOW


def test_유지_시간이_지나면_직전_판정을_놓는다() -> None:
    previous = _decide((_evidence("seat-1", student_id=STUDENT, identity_confidence=0.8),))

    record = _decide(
        (_evidence("seat-1"),),
        previous=previous,
        observed_at=NOW + timedelta(seconds=20),
    )

    assert record.state == StudentState.UNKNOWN
    assert record.reason == StudentStateReason.SEAT_OCCUPIED_BY_UNKNOWN


def test_그_좌석이_빈_것을_보면_유지_시간이_남아도_놓는다() -> None:
    """자리를 뜬 것이 확인됐다. 붙들 근거가 사라졌으므로 계속 재석으로 두지 않는다."""
    previous = _decide((_evidence("seat-1", student_id=STUDENT, identity_confidence=0.8),))

    record = _decide(
        (_evidence("seat-1", occupied=False, confidence=0.0),),
        previous=previous,
        observed_at=NOW + timedelta(seconds=3),
    )

    assert record.state == StudentState.UNKNOWN
    assert record.reason == StudentStateReason.SEAT_VACANT_WITHIN_GRACE


def test_빈_좌석은_유예_시간_안에서는_결석이_아니다() -> None:
    record = _decide((_evidence("seat-1", occupied=False, confidence=0.0),))

    assert record.state == StudentState.UNKNOWN
    assert record.reason == StudentStateReason.SEAT_VACANT_WITHIN_GRACE
    assert record.vacant_since == NOW


def test_빈_좌석을_유예_시간_내내_보면_결석이_된다() -> None:
    """`ABSENT`는 비어 있는 것을 계속 **본** 결과다. 안 본 시간은 세지 않는다."""
    record = _decide((_evidence("seat-1", occupied=False, confidence=0.0),))
    assert record.state == StudentState.UNKNOWN

    for seconds in (100, 200, 299):
        record = _decide(
            (_evidence("seat-1", occupied=False, confidence=0.0),),
            previous=record,
            observed_at=NOW + timedelta(seconds=seconds),
        )
        assert record.state == StudentState.UNKNOWN

    record = _decide(
        (_evidence("seat-1", occupied=False, confidence=0.0),),
        previous=record,
        observed_at=NOW + timedelta(seconds=300),
    )

    assert record.state == StudentState.ABSENT
    assert record.reason == StudentStateReason.SEAT_VACANT_BEYOND_GRACE
    assert record.vacant_since == NOW


def test_좌석을_보지_못하면_결석이_아니라_모름이다() -> None:
    """미관측을 부재로 바꾸지 않는다(결정 0008).

    ROI가 없거나 카메라가 그 자리를 못 보면 관측 자체가 만들어지지 않는다. 유예 시간이
    아무리 지나도 결석이 되지 않아야 한다.
    """
    record = _decide((_evidence("seat-2", occupied=False, confidence=0.0),))
    for seconds in (300, 600, 900):
        record = _decide(
            (_evidence("seat-2", occupied=False, confidence=0.0),),
            previous=record,
            observed_at=NOW + timedelta(seconds=seconds),
        )

    assert record.state == StudentState.UNKNOWN
    assert record.reason == StudentStateReason.SEAT_NOT_OBSERVED


def test_임계값_미만_점유는_결석_유예를_시작하지_않는다() -> None:
    """흐릿하게 사람이 잡힌 자리를 "비었다"로 세면 결석 시계가 잘못 돈다."""
    record = _decide((_evidence("seat-1", confidence=0.3),))

    assert record.state == StudentState.UNKNOWN
    assert record.reason == StudentStateReason.SEAT_NOT_OBSERVED
    assert record.vacant_since is None


def test_같은_학생이_두_좌석에_잡히면_신원_신뢰도가_높은_쪽을_쓴다() -> None:
    record = _decide(
        (
            _evidence("seat-1", student_id=STUDENT, identity_confidence=0.6),
            _evidence("seat-2", student_id=STUDENT, identity_confidence=0.9),
        ),
    )

    assert record.state == StudentState.WRONG_SEAT
    assert record.seat_id == "seat-2"
    assert record.confidence == 0.9


def test_근거가_오래되면_화면에서는_모름으로_가린다() -> None:
    """카메라가 죽은 뒤에도 재석을 계속 보여 주는 것이 가장 위험한 표시다."""
    record = _decide((_evidence("seat-1", student_id=STUDENT, identity_confidence=0.8),))

    fresh = project_for_display(record, NOW + timedelta(seconds=600), POLICY)
    stale = project_for_display(record, NOW + timedelta(seconds=601), POLICY)

    assert fresh.state == StudentState.PRESENT
    assert fresh.seat_id == "seat-1"
    assert stale.state == StudentState.UNKNOWN
    assert stale.reason == StudentStateReason.SEAT_NOT_OBSERVED
    # 상태만 모른다고 하면서 자리는 알려 주면 화면이 서로 어긋난다.
    assert stale.seat_id is None
    assert stale.confidence is None


def test_오래된_근거를_가려도_저장된_판정은_그대로다() -> None:
    """조회가 저장된 상태를 바꾸지 않는다(결정 0008)."""
    record = _decide((_evidence("seat-1", student_id=STUDENT, identity_confidence=0.8),))

    project_for_display(record, NOW + timedelta(days=1), POLICY)

    assert record.state == StudentState.PRESENT
    assert record.seat_id == "seat-1"
