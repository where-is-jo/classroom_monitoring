"""TASK-004: 좌석 관리 UI 계약 정적 검증 (HTML contract) 테스트.

통합 좌석 관리 화면(`/classrooms/{id}/seats`)이 확정 계약(SPEC UI 바인딩)을
렌더링하는지 검증한다.

- tray 섹션·unplace 버튼·is_active 입력·code 입력이 HTML에 없다.
- danger 버튼(해제·삭제)이 HTML에 있다.
- aria-live region(상태·오류)이 HTML에 있다.
- 모든 인터랙티브 요소가 native button/input/select다 (키보드 접근성).

JS 동작(빈 칸 클릭 → POST /seats/auto, 재시도 없음)은
`test_seat_admin_js.py`의 headless 브라우저 검증이 담당한다.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import cast

import pytest
from fastapi.testclient import TestClient

from app.classrooms.adapters.memory_repository import (
    InMemoryClassroomRepository,
    InMemorySeatAssignmentRepository,
)
from app.classrooms.service import ClassroomService
from app.main import app
from app.shared.adapters.memory_student_lookup import InMemoryStudentLookup
from app.shared.dependencies import get_classroom_service, get_student_lookup
from app.shared.student_identity import StudentIdentity


@pytest.fixture
def client() -> Iterator[TestClient]:
    student_lookup = InMemoryStudentLookup(
        identities=(
            StudentIdentity(id="stu-001", student_no="20240001", name="김철수", is_active=True),
        )
    )
    service = ClassroomService(
        InMemoryClassroomRepository(),
        student_lookup=student_lookup,
        assignment_repository=InMemorySeatAssignmentRepository(),
        occupancy_confidence_threshold=0.5,
        clock=lambda: datetime(2026, 8, 13, 9, 0, tzinfo=UTC),
    )
    app.dependency_overrides[get_classroom_service] = lambda: service
    app.dependency_overrides[get_student_lookup] = lambda: student_lookup
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _create_classroom(
    client: TestClient,
    *,
    code: str = "A101",
    name: str = "좌석 UI 강의실",
    location: str = "A동 1층",
) -> str:
    response = client.post(
        "/api/v1/classrooms",
        json={"code": code, "name": name, "location": location},
    )
    assert response.status_code == 201
    return str(cast(dict[str, object], response.json())["id"])


def _create_seat(
    client: TestClient,
    classroom_id: str,
    *,
    code: str,
    label: str,
    row: int | None = None,
    column: int | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {"code": code, "label": label}
    if row is not None:
        payload["row"] = row
    if column is not None:
        payload["column"] = column
    response = client.post(f"/api/v1/classrooms/{classroom_id}/seats", json=payload)
    assert response.status_code == 201
    return cast(dict[str, object], response.json())


def _render_seats_page(client: TestClient) -> str:
    """좌표가 있는 좌석 1개 + 좌표가 없는 좌석 1개를 둔 화면 HTML을 돌려준다.

    좌표 없는(legacy) 좌석이 존재해도 tray가 렌더링되지 않아야 하므로
    양쪽 데이터를 모두 준비한다. (1,1)은 빈 칸으로 남겨 auto 생성 클릭
    대상(seat-cell-1-1)이 존재하게 한다.
    """
    classroom_id = _create_classroom(client)
    _create_seat(client, classroom_id, code="S01", label="좌석 1", row=1, column=2)
    _create_seat(client, classroom_id, code="S02", label="좌석 2")
    response = client.get(f"/classrooms/{classroom_id}/seats")
    assert response.status_code == 200
    return cast(str, response.text)


# ============================================================
# tray / unplace / is_active / code 입력 제거
# ============================================================


def test_seats_page_has_no_tray_section(client: TestClient) -> None:
    """배치 대기 영역(tray)이 HTML에 없다. 좌표 없는 좌석이 있어도 마찬가지다."""
    html = _render_seats_page(client)
    assert "seat-tray" not in html
    assert "배치 대기" not in html
    assert "tray-seat-" not in html


def test_seats_page_has_no_unplace_buttons(client: TestClient) -> None:
    """격자 밖 배치 해제(unplace) 버튼이 HTML에 없다."""
    html = _render_seats_page(client)
    assert "seat-unplace" not in html
    assert "unplace-" not in html


def test_seats_page_has_no_is_active_input(client: TestClient) -> None:
    """편집 패널에 is_active 입력이 없다 (payload에도 보내지 않는다)."""
    html = _render_seats_page(client)
    assert 'name="is_active"' not in html


def test_seats_page_has_no_code_input(client: TestClient) -> None:
    """편집 패널에 code 입력이 없다. code는 서버 allocator가 할당한다."""
    html = _render_seats_page(client)
    assert 'name="code"' not in html
    assert 'placeholder="S01"' not in html


# ============================================================
# danger 버튼 / aria-live
# ============================================================


def test_seats_page_has_danger_release_button(client: TestClient) -> None:
    """해제(release)는 링크가 아닌 visible danger 버튼이다 (SPEC UI 바인딩)."""
    html = _render_seats_page(client)
    assert '<button type="button" id="btn-unassign" class="danger">' in html


def test_seats_page_has_danger_delete_button(client: TestClient) -> None:
    """삭제(delete)는 링크가 아닌 visible danger 버튼이다 (SPEC UI 바인딩)."""
    html = _render_seats_page(client)
    assert '<button type="button" id="btn-delete" class="danger">' in html


def test_seats_page_has_aria_live_regions(client: TestClient) -> None:
    """성공 안내(polite)와 오류(role=alert) aria-live region이 존재한다."""
    html = _render_seats_page(client)
    assert 'id="seat-grid-status"' in html
    assert 'aria-live="polite"' in html
    assert 'id="seat-grid-alert"' in html
    assert 'role="alert"' in html


def test_seats_page_has_form_error_alert(client: TestClient) -> None:
    """편집 패널의 오류 표시도 role=alert로 접근 가능하다."""
    html = _render_seats_page(client)
    assert "data-form-error" in html
    assert 'role="alert"' in html


# ============================================================
# 키보드 접근성 (native button / focusable)
# ============================================================


def test_seats_page_all_interactive_elements_are_native(client: TestClient) -> None:
    """격자 셀·툴바·패널 컨트롤은 전부 native button/input/select다.

    tabindex 없이도 Enter·Space로 조작 가능해야 하므로 모든 인터랙티브
    요소가 native 컨트롤임을 정적으로 확인한다.
    """
    html = _render_seats_page(client)
    # 격자 셀은 button (빈 칸·좌석 모두)
    assert 'id="seat-cell-1-1"' in html
    assert 'class="seat-map__empty"' in html
    # 모든 <button>은 type이 명시되어 있다 (기본 submit 방지)
    buttons = re.findall(r"<button[^>]*>", html)
    assert buttons, "button이 하나도 없다"
    assert all('type="button"' in tag or 'type="submit"' in tag for tag in buttons)
    # 패널 컨트롤은 native input/select/button이다
    assert 'name="label"' in html
    assert 'name="student_id"' in html
    assert 'id="add-row"' in html
    assert 'id="add-column"' in html
    # 스크롤 영역은 tabindex=0으로 focusable하다
    assert 'role="region"' in html
    assert 'tabindex="0"' in html


def test_seats_page_empty_cells_are_native_buttons(client: TestClient) -> None:
    """빈 칸은 native button이며 row/column data를 가진다 (auto 생성 입력값)."""
    html = _render_seats_page(client)
    empty = re.search(
        r'<button type="button" id="seat-cell-1-1" class="seat-map__empty" '
        r'data-row="1" data-column="1"[^>]*>',
        html,
    )
    assert empty is not None, "빈 칸 셀이 native button으로 렌더링되지 않았다"
