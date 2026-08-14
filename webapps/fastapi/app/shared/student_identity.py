"""중립 학생 조회 계약.

`classrooms`·`student_monitoring`이 `app.students`에 의존하지 않도록,
학생 조회에 필요한 최소 계약만 `app/shared`에 둔다.
쓰기·전체목록(비활성 포함)·번호조회·name/student_no 외 추가 PII는 제공하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .errors import ClassroomInputError


@dataclass(frozen=True)
class StudentIdentity:
    """학생 조회용 최소 식별 정보.

    id, student_no, name, is_active 외의 PII(소속·생성 시각 등)는 없다.
    """

    id: str
    student_no: str
    name: str
    is_active: bool


@dataclass(frozen=True)
class StudentIdentityPage:
    """active 학생 목록 페이지네이션 결과.

    total은 pagination을 적용하기 전 active 학생 전체 수다.
    """

    items: list[StudentIdentity]
    total: int


class StudentLookupPort(Protocol):
    """학생 조회 포트.

    - unknown은 `None`, inactive는 알려진 객체로 돌려준다.
    - 목록은 active-only·결정적 순서이며 `total`은 pagination 전 값이다.
    - 쓰기·전체목록·번호조회·추가 PII를 제공하지 않는다.
    """

    def find_by_id(self, student_id: str) -> StudentIdentity | None:
        """ID로 학생을 조회한다. unknown은 None이다."""
        ...

    def list_active(self, *, limit: int, offset: int) -> StudentIdentityPage:
        """활성 학생을 결정적 순서로 페이지네이션해 조회한다."""
        ...


def validate_list_active_args(
    *, limit: int, offset: int, page_size_max: int
) -> None:
    """호출자 입력 검증. 위반 시 422 CLASSROOM_INPUT_INVALID를 던진다.

    계약: `0 <= offset`, `1 <= limit <= Settings.page_size_max`.
    """
    if offset < 0:
        raise ClassroomInputError("offset은 0 이상이어야 합니다.")
    if limit < 1:
        raise ClassroomInputError("limit은 1 이상이어야 합니다.")
    if limit > page_size_max:
        raise ClassroomInputError(f"limit은 {page_size_max} 이하여야 합니다.")
