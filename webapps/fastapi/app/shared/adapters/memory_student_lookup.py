"""중립 학생 조회 in-memory 어댑터.

명시적 DI·테스트 fixture로 `identities`를 주입하며, runtime 기본은 empty다.
등록/수정/삭제 등 쓰기 기능, 전체목록(비활성 포함), 번호조회는 없다.
"""

from __future__ import annotations

from ..student_identity import StudentIdentity, StudentIdentityPage


class InMemoryStudentLookup:
    """읽기 전용 학생 조회 어댑터.

    - `find_by_id`: unknown은 `None`, inactive는 알려진 객체를 돌려준다.
    - `list_active`: active-only이며 id 기준 결정적 순서로 페이지네이션한다.
    """

    def __init__(self, identities: tuple[StudentIdentity, ...] = ()) -> None:
        self._by_id: dict[str, StudentIdentity] = {identity.id: identity for identity in identities}

    def find_by_id(self, student_id: str) -> StudentIdentity | None:
        return self._by_id.get(student_id)

    def list_active(self, *, limit: int, offset: int) -> StudentIdentityPage:
        active = sorted(
            (identity for identity in self._by_id.values() if identity.is_active),
            key=lambda identity: identity.id,
        )
        return StudentIdentityPage(
            items=active[offset : offset + limit],
            total=len(active),
        )
