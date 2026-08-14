"""중립 학생 조회 계약 테스트 (TASK-002).

계약 내용:
- frozen `StudentIdentity(id, student_no, name, is_active)` / `StudentIdentityPage(items, total)`
- `StudentLookupPort.find_by_id` → unknown은 `None`, inactive는 알려진 객체
- `StudentLookupPort.list_active` → active-only·결정적 순서·`total`은 pagination 전 값
- 호출자 입력 검증 → 422 `CLASSROOM_INPUT_INVALID`
- 쓰기·전체목록(비활성 포함)·번호조회·추가 PII 금지
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields

import pytest

from app.shared.adapters.memory_student_lookup import InMemoryStudentLookup
from app.shared.errors import (
    ClassroomInputError,
    StudentInactiveForAssignmentError,
    StudentNotFoundError,
)
from app.shared.student_identity import (
    StudentIdentity,
    StudentIdentityPage,
    validate_list_active_args,
)


def _lookup_with_fixture() -> InMemoryStudentLookup:
    return InMemoryStudentLookup(
        identities=(
            StudentIdentity(id="stu-b", student_no="20260202", name="이지원", is_active=True),
            StudentIdentity(id="stu-a", student_no="20260101", name="홍길동", is_active=True),
            StudentIdentity(id="stu-c", student_no="20269999", name="김비활성", is_active=False),
        )
    )


class TestStudentIdentityContract:
    """StudentIdentity 모델 계약."""

    def test_identity_is_frozen(self) -> None:
        identity = StudentIdentity(id="stu-1", student_no="20260101", name="홍길동", is_active=True)
        with pytest.raises(FrozenInstanceError):
            identity.name = "이름변경"  # type: ignore[misc]

    def test_identity_has_only_minimal_fields(self) -> None:
        """추가 PII(department·created_at·updated_at 등)는 없다."""
        names = {field.name for field in fields(StudentIdentity)}
        assert names == {"id", "student_no", "name", "is_active"}

    def test_page_has_items_and_total(self) -> None:
        page = StudentIdentityPage(items=[], total=0)
        assert page.items == []
        assert page.total == 0


class TestInMemoryStudentLookup:
    """InMemoryStudentLookup 동작 계약."""

    def test_find_by_id_returns_none_for_unknown(self) -> None:
        lookup = _lookup_with_fixture()
        assert lookup.find_by_id("stu-unknown") is None

    def test_find_by_id_returns_known_object_for_inactive(self) -> None:
        """inactive 학생은 None이 아니라 알려진 객체로 돌려준다."""
        lookup = _lookup_with_fixture()
        inactive = lookup.find_by_id("stu-c")
        assert inactive is not None
        assert inactive.name == "김비활성"
        assert inactive.is_active is False

    def test_find_by_id_returns_active_student(self) -> None:
        lookup = _lookup_with_fixture()
        student = lookup.find_by_id("stu-a")
        assert student is not None
        assert student.student_no == "20260101"

    def test_list_active_returns_only_active_students(self) -> None:
        lookup = _lookup_with_fixture()
        page = lookup.list_active(limit=10, offset=0)
        assert [item.id for item in page.items] == ["stu-a", "stu-b"]
        assert all(item.is_active for item in page.items)

    def test_list_active_is_deterministic_order(self) -> None:
        """결정적 순서: id 기준 정렬이며 주입 순서와 무관하다."""
        shuffled = InMemoryStudentLookup(
            identities=(
                StudentIdentity(id="stu-b", student_no="20260202", name="이지원", is_active=True),
                StudentIdentity(id="stu-a", student_no="20260101", name="홍길동", is_active=True),
            )
        )
        page = shuffled.list_active(limit=10, offset=0)
        assert [item.id for item in page.items] == ["stu-a", "stu-b"]

    def test_list_active_total_is_pre_pagination_count(self) -> None:
        """total은 pagination을 적용하기 전 active 학생 전체 수다."""
        lookup = _lookup_with_fixture()
        page = lookup.list_active(limit=1, offset=0)
        assert [item.id for item in page.items] == ["stu-a"]
        assert page.total == 2

    def test_list_active_pagination_offset(self) -> None:
        lookup = _lookup_with_fixture()
        page = lookup.list_active(limit=1, offset=1)
        assert [item.id for item in page.items] == ["stu-b"]
        assert page.total == 2

    def test_list_active_empty_default(self) -> None:
        """runtime 기본은 empty다."""
        lookup = InMemoryStudentLookup()
        page = lookup.list_active(limit=10, offset=0)
        assert page.items == []
        assert page.total == 0
        assert lookup.find_by_id("any") is None

    def test_no_write_or_extra_read_apis(self) -> None:
        """쓰기·전체목록(비활성 포함)·번호조회 API가 없다."""
        lookup = InMemoryStudentLookup()
        assert not hasattr(lookup, "create")
        assert not hasattr(lookup, "update")
        assert not hasattr(lookup, "delete")
        assert not hasattr(lookup, "list_all")
        assert not hasattr(lookup, "list_students")
        assert not hasattr(lookup, "find_by_student_no")


class TestInputValidationContract:
    """호출자 입력 검증 → 422 CLASSROOM_INPUT_INVALID."""

    def test_offset_below_zero_raises(self) -> None:
        with pytest.raises(ClassroomInputError, match="offset"):
            validate_list_active_args(limit=10, offset=-1, page_size_max=200)

    def test_limit_below_one_raises(self) -> None:
        with pytest.raises(ClassroomInputError, match="limit"):
            validate_list_active_args(limit=0, offset=0, page_size_max=200)

    def test_limit_above_page_size_max_raises(self) -> None:
        with pytest.raises(ClassroomInputError, match="limit"):
            validate_list_active_args(limit=201, offset=0, page_size_max=200)

    def test_valid_args_pass(self) -> None:
        validate_list_active_args(limit=200, offset=0, page_size_max=200)
        validate_list_active_args(limit=1, offset=0, page_size_max=200)

    def test_input_error_maps_to_422_classroom_input_invalid(self) -> None:
        error = ClassroomInputError("limit은 1~200 사이여야 합니다.")
        assert error.code == "CLASSROOM_INPUT_INVALID"
        assert error.status_code == 422


class TestSharedErrorCodes:
    """neutral shared error 계약."""

    def test_student_not_found_is_404(self) -> None:
        assert StudentNotFoundError().code == "STUDENT_NOT_FOUND"
        assert StudentNotFoundError().status_code == 404

    def test_student_inactive_for_assignment_is_422(self) -> None:
        assert StudentInactiveForAssignmentError().code == "STUDENT_INACTIVE_FOR_ASSIGNMENT"
        assert StudentInactiveForAssignmentError().status_code == 422
