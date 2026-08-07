"""공통 Jinja 표시 helper 테스트."""

from datetime import UTC, datetime

from app.employees.models import EmployeeStatus
from app.shared.templating import format_display_label, format_kst
from app.users.models import UserRole


def test_display_label_translates_known_enum_and_preserves_unknown_value() -> None:
    assert format_display_label(EmployeeStatus.WORKING) == "근무 중"
    assert format_display_label(UserRole.SYSTEM_OPERATOR) == "시스템 운영자"
    assert format_display_label("CUSTOM_EVENT") == "CUSTOM_EVENT"
    assert format_display_label(None) == "-"


def test_format_kst_keeps_existing_display_contract() -> None:
    value = datetime(2026, 8, 6, 0, 0, tzinfo=UTC)
    assert format_kst(value) == "2026-08-06 09:00:00 KST"
    assert format_kst(None) == "-"
