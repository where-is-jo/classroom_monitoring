"""검색 한 번을 사람이 읽는 문장으로 적는 규칙.

화면 없이 문장만 고정한다. 여기가 갈라지면 사용자는 자기가 물은 시각과 결과를
대조할 근거를 잃는다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.llm_search.briefing import build_briefing, format_period, josa
from app.llm_search.models import PersonPresence, PersonSummary


def _at(hour: int, minute: int = 0, second: int = 0) -> datetime:
    """2026-08-24 KST의 시각 하나를 저장 형식(UTC)으로 돌려준다.

    서비스가 `build_briefing`에 넘기는 값과 같은 모양이다 — 저장은 UTC이고 표시만
    KST다. `hour`는 24를 넘겨도 되며 다음 날로 넘어간다.
    """
    kst_midnight = datetime(2026, 8, 24, tzinfo=UTC) - _NINE_HOURS
    return kst_midnight + timedelta(hours=hour, minutes=minute, seconds=second)


_NINE_HOURS = timedelta(hours=9)


def _person(
    *,
    name: str = "박무현",
    presence: PersonPresence = PersonPresence.ABSENT,
    student_id: str | None = "student-1",
    match_count: int = 1,
    identity_available: bool = True,
    applied: bool = True,
) -> PersonSummary:
    return PersonSummary(
        name=name,
        presence=presence,
        student_id=student_id,
        match_count=match_count,
        identity_available=identity_available,
        applied=applied,
    )


def test_같은_날이면_날짜를_한_번만_쓴다() -> None:
    """같은 정보를 두 번 읽게 만들면 정작 다른 부분인 분 단위가 눈에 들어오지 않는다."""
    assert format_period(_at(16, 30), _at(17)) == "2026년 8월 24일 16:30~17:00"


def test_분을_버리지_않는다() -> None:
    """ "16시 30분"을 물었는데 문장이 16:00으로 보이면 잘못 해석된 것을 알아챌 수 없다."""
    assert "16:30" in format_period(_at(16, 30), _at(17))


def test_초는_0이_아닐_때만_보인다() -> None:
    """대부분의 질문은 분 단위다. 매 줄에 ':00'이 붙으면 자릿수만 늘어난다."""
    assert format_period(_at(16, 30), _at(17)) == "2026년 8월 24일 16:30~17:00"
    assert format_period(_at(16, 30, 20), _at(17)) == "2026년 8월 24일 16:30:20~17:00:00"


def test_날짜를_넘기면_양쪽_날짜를_모두_쓴다() -> None:
    assert format_period(_at(22), _at(26)) == "2026년 8월 24일 22:00부터 8월 25일 02:00까지"


def test_해가_같으면_뒤쪽_날짜에_연도를_붙이지_않는다() -> None:
    period = format_period(_at(22), _at(26))

    assert period.count("2026년") == 1


def test_기간과_대상과_건수를_한_문장씩_적는다() -> None:
    briefing = build_briefing(
        from_at=_at(16, 30),
        to_at=_at(17),
        target_label="A111 4A 강의실",
        person=None,
        hit_count=3,
        truncated=False,
    )

    assert briefing == (
        "2026년 8월 24일 16:30~17:00 동안 A111 4A 강의실에서 찾았어요. 총 3건의 결과가 있어요."
    )


def test_결과가_없으면_0건이라고_적지_않는다() -> None:
    """ "총 0건의 결과가 있어요"는 읽는 사람에게 걸린다."""
    briefing = build_briefing(
        from_at=_at(16),
        to_at=_at(17),
        target_label="A111 4A 강의실",
        person=None,
        hit_count=0,
        truncated=False,
    )

    assert briefing.endswith("결과는 없어요.")


def test_잘렸으면_건수보다_그_사실을_앞세운다() -> None:
    """ "총 20건"이라고만 하면 그것이 전부라는 뜻이 된다. 가장 위험한 오해다."""
    briefing = build_briefing(
        from_at=_at(16),
        to_at=_at(17),
        target_label="A111 4A 강의실",
        person=None,
        hit_count=20,
        truncated=True,
    )

    assert "이게 전부는 아니에요" in briefing


def test_인물_조건을_걸었으면_어느_쪽인지_적는다() -> None:
    absent = build_briefing(
        from_at=_at(16, 30),
        to_at=_at(17),
        target_label="A111 4A 강의실",
        person=_person(presence=PersonPresence.ABSENT),
        hit_count=2,
        truncated=False,
    )
    present = build_briefing(
        from_at=_at(16, 30),
        to_at=_at(17),
        target_label="A111 4A 강의실",
        person=_person(presence=PersonPresence.PRESENT),
        hit_count=2,
        truncated=False,
    )

    assert "박무현이 보이지 않는 기록만 골랐고" in absent
    assert "박무현이 보이는 기록만 골랐고" in present


def test_조건을_걸지_못했으면_걸었다고_말하지_않는다() -> None:
    """걸러지지 않은 목록에 "없는 기록"이라는 설명이 붙으면 없는 판정을 만들어 낸다."""
    briefing = build_briefing(
        from_at=_at(16, 30),
        to_at=_at(17),
        target_label="A111 4A 강의실",
        person=_person(identity_available=False, applied=False),
        hit_count=9,
        truncated=False,
    )

    assert "얼굴 인식이 아직 연결되지 않아" in briefing
    assert "골랐고" not in briefing


def test_명부에_없는_이름은_그_사실을_적는다() -> None:
    briefing = build_briefing(
        from_at=_at(16, 30),
        to_at=_at(17),
        target_label="A111 4A 강의실",
        person=_person(student_id=None, match_count=0, applied=False),
        hit_count=9,
        truncated=False,
    )

    assert "학생 명부에서 찾지 못해" in briefing


def test_동명이인은_명부에_없는_것과_다르게_적는다() -> None:
    """오타를 고치면 되는 상황과, 이름만으로는 영영 고를 수 없는 상황은 할 일이 다르다."""
    briefing = build_briefing(
        from_at=_at(16, 30),
        to_at=_at(17),
        target_label="A111 4A 강의실",
        person=_person(student_id=None, match_count=2, applied=False),
        hit_count=9,
        truncated=False,
    )

    assert "2명이라 누구인지 정하지 못했고" in briefing
    assert "찾지 못해" not in briefing


@pytest.mark.parametrize(
    ("name", "expected"),
    [("박무현", "박무현이"), ("김서아", "김서아가"), ("한별", "한별이")],
)
def test_받침에_맞는_조사를_고른다(name: str, expected: str) -> None:
    """ "박무현가 보이는"은 읽는 사람에게 기계가 쓴 문장으로 보인다."""
    assert f"{name}{josa(name, '이', '가')}" == expected


def test_한글이_아니면_한쪽으로_고정한다() -> None:
    """맞는 조사를 정할 수 없는 값이다. 어색해지는 것과 조사가 빠지는 것 중 앞을 택한다."""
    assert josa("Alex", "이", "가") == "이"
    assert josa("", "을", "를") == "을"
