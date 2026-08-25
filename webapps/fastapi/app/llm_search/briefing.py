"""검색 한 번을 사람이 읽는 문장으로 적는다. 순수 함수다.

기간·대상·인물 조건·건수는 `SearchQuery`·`target_label`·`PersonSummary`·`hits`에
흩어져 있다. 그것을 문장으로 잇는 일을 화면에 두면 **표기 규칙이 템플릿으로 샌다** —
같은 날이면 날짜를 한 번만 쓸지, 초를 보일지, 0건을 뭐라고 부를지가 전부 판단이고,
화면이 늘 때마다 복사된다. 결정 0001이 "임계값 해석·분류는 서비스에서 끝낸다"고
정한 것과 같은 자리다.

## 왜 조사(을/를, 이/가)를 계산하는가

"박무현를 찾지 못했습니다"는 읽는 사람에게 기계가 쓴 문장으로 보인다. 이 화면의
목적이 "무엇을 어떻게 이해했는지"를 사용자가 **한 번에 읽고 넘기게** 하는 것이라,
문장이 걸리면 목적을 잃는다. 사람 이름은 한글이 보장되므로 받침 계산이 정확하다.

대상(강의실·카메라)에는 조사를 계산하지 않는다. `target_label`은 "A111 4A 강의실",
"카메라 정문 (camera-01)", "사용 중인 카메라 전체"처럼 끝 글자가 한글일 수도 괄호일
수도 있어 받침을 알 수 없다. 그래서 **받침과 무관한 "에서"만 붙인다.**

## 왜 KST로 적고 KST라고 쓰지 않는가

이 시스템이 다루는 강의실은 한국에 있고 화면의 모든 시각이 KST다(`prompts.KST`).
매 줄에 같은 꼬리표를 붙이면 읽는 눈이 그것을 걸러 내야 하고, 정작 봐야 할 분·초가
묻힌다. 대신 저장 값은 UTC 그대로 남아 있고 API 응답도 UTC로 나간다 — 바뀌는 것은
사람이 읽는 문장뿐이다.
"""

from __future__ import annotations

from datetime import datetime

from .models import PersonPresence, PersonSummary
from .prompts import KST

__all__ = ["build_briefing", "format_period", "josa"]


def build_briefing(
    *,
    from_at: datetime,
    to_at: datetime,
    target_label: str,
    person: PersonSummary | None,
    hit_count: int,
    truncated: bool,
) -> str:
    """이번 검색을 두 문장으로 적는다.

    앞 문장은 **무엇을 어떻게 이해했는지**, 뒤 문장은 **그래서 몇 건인지**다. 둘을
    한 문장에 욱여넣지 않는 이유는 사용자가 확인하는 순서가 그렇기 때문이다 — 기간이
    틀렸으면 건수는 볼 필요가 없다.
    """
    sentences = [f"{format_period(from_at, to_at)} 동안 {target_label}에서 찾았어요."]
    person_clause = _person_clause(person)
    sentences.append(f"{person_clause}{_count_clause(hit_count, truncated)}")
    return " ".join(sentences)


def format_period(from_at: datetime, to_at: datetime) -> str:
    """구간을 한국 시각으로 적는다.

    같은 날이면 날짜를 한 번만 쓴다. "2026년 8월 24일 16:30~2026년 8월 24일 17:00"은
    같은 정보를 두 번 읽게 만들어, 정작 다른 부분인 분 단위가 눈에 들어오지 않는다.

    초는 **어느 한쪽이라도 0이 아닐 때만** 보인다. 대부분의 질문은 분 단위라 매 줄에
    ":00"이 붙으면 자릿수만 늘어나고, 반대로 초를 물은 사람에게는 그것이 유일하게
    확인해야 할 자리다.
    """
    start = from_at.astimezone(KST)
    end = to_at.astimezone(KST)
    with_seconds = bool(start.second or end.second)

    if start.date() == end.date():
        return (
            f"{_date_text(start)} {_time_text(start, with_seconds)}~{_time_text(end, with_seconds)}"
        )
    same_year = start.year == end.year
    end_date = _date_text(end, with_year=not same_year)
    return (
        f"{_date_text(start)} {_time_text(start, with_seconds)}부터 "
        f"{end_date} {_time_text(end, with_seconds)}까지"
    )


def josa(word: str, with_final: str, without_final: str) -> str:
    """받침에 맞는 조사를 고른다. 한글이 아니면 받침 있는 쪽으로 둔다.

    한글이 아닌 값이 들어오는 경우는 사람 이름이 아니라 모델이 엉뚱한 문자열을
    옮긴 때다. 그런 값에 맞는 조사는 정할 수 없으므로 한쪽으로 고정한다 —
    문장이 조금 어색해지는 것과 조사가 통째로 빠지는 것 중 앞쪽을 택한다.
    """
    if not word:
        return with_final
    last = word[-1]
    if not ("\uac00" <= last <= "\ud7a3"):
        return with_final
    return without_final if (ord(last) - 0xAC00) % 28 == 0 else with_final


def _person_clause(person: PersonSummary | None) -> str:
    """인물 조건을 앞에 덧붙인다. **적용하지 못했으면 그 사실을 적는다.**

    걸러 내지 못한 목록에 "박무현이 없는 기록"이라는 설명이 붙으면, 우리가 하지 않은
    판정을 한 것처럼 보인다. 지금은 얼굴 인식이 연결되지 않아 이 경로가 기본이다.
    """
    if person is None:
        return ""
    subject = josa(person.name, "이", "가")
    if person.match_count > 1:
        # "명부에 없습니다"와 섞으면 안 된다. 오타를 고치면 되는 상황과, 이름만으로는
        # 영영 고를 수 없는 상황은 사용자가 할 일이 다르다.
        return f"{person.name}{subject} {person.match_count}명이라 누구인지 정하지 못했고, "
    if person.student_id is None:
        objective = josa(person.name, "을", "를")
        return f"{person.name}{objective} 학생 명부에서 찾지 못해 인물 조건은 걸지 못했고, "
    if not person.applied:
        return f"얼굴 인식이 아직 연결되지 않아 {person.name}{subject} 있었는지는 확인하지 못했고, "
    if person.presence is PersonPresence.ABSENT:
        return f"{person.name}{subject} 보이지 않는 기록만 골랐고, "
    return f"{person.name}{subject} 보이는 기록만 골랐고, "


def _count_clause(hit_count: int, truncated: bool) -> str:
    if truncated:
        # "총 20건"이라고만 하면 그것이 전부라는 뜻이 된다. 잘린 목록에서 가장 위험한
        # 오해라 건수보다 앞세운다.
        return f"상한에 걸려 {hit_count}건까지만 보여드려요. 이게 전부는 아니에요."
    if hit_count == 0:
        return "결과는 없어요."
    return f"총 {hit_count}건의 결과가 있어요."


def _date_text(moment: datetime, *, with_year: bool = True) -> str:
    if with_year:
        return f"{moment.year}년 {moment.month}월 {moment.day}일"
    return f"{moment.month}월 {moment.day}일"


def _time_text(moment: datetime, with_seconds: bool) -> str:
    if with_seconds:
        return moment.strftime("%H:%M:%S")
    return moment.strftime("%H:%M")
