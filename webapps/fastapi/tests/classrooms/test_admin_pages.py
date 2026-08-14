"""강의실·좌석 관리 화면 렌더링과 입력 검증 테스트.

관리 화면 폼은 JSON API(`/api/v1/...`)에 fetch로 제출된다. 그래서 화면 테스트는
① 페이지가 올바른 폼과 API 배선(`data-api-url`·`data-api-method`)을 렌더링하는지,
② 대상이 없으면 목록으로 리다이렉트하는지, ③ 화면이 표시할 API 검증 오류가
message를 포함하는지 확인한다. 실제 검증 로직은 `test_crud_api.py`가 담당한다.
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
from app.shared.dependencies import get_classroom_service, get_settings, get_student_lookup
from app.shared.student_identity import StudentIdentity, StudentIdentityPage

# 좌석-학생 지정 화면의 학생 선택 select는 중립 조회 seam(StudentLookupPort.list_active)으로
# 활성 학생만 조회한다. CRUD API(TASK-004 제거 대상)에 의존하지 않도록 시드된 identity를 쓴다.
_SEEDED_STUDENTS: dict[str, StudentIdentity] = {
    "20240001": StudentIdentity(
        id="stu-001",
        student_no="20240001",
        name="김철수",
        is_active=True,
    ),
    "20240002": StudentIdentity(
        id="stu-002",
        student_no="20240002",
        name="이영희",
        is_active=False,
    ),
}


class RecordingStudentLookup:
    """InMemoryStudentLookup을 감싸 list_active 호출 인자를 기록한다.

    assignment page가 단일 `list_active(max,0)`을 호출하는지 검증하기 위한
    테스트 전용 wrapper다. 실제 조회 동작은 내부 어댑터에 위임한다.
    """

    def __init__(self, inner: InMemoryStudentLookup) -> None:
        self._inner = inner
        self.list_active_calls: list[tuple[int, int]] = []

    def find_by_id(self, student_id: str) -> StudentIdentity | None:
        return self._inner.find_by_id(student_id)

    def list_active(self, *, limit: int, offset: int) -> StudentIdentityPage:
        self.list_active_calls.append((limit, offset))
        return self._inner.list_active(limit=limit, offset=offset)


@pytest.fixture
def client() -> Iterator[TestClient]:
    # 중립 조회 어댑터를 명시적으로 주입한다 (DI/test fixture).
    student_lookup = InMemoryStudentLookup(identities=tuple(_SEEDED_STUDENTS.values()))
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
    name: str = "일반 강의실",
    location: str = "A동 1층",
) -> dict[str, object]:
    response = client.post(
        "/api/v1/classrooms",
        json={"code": code, "name": name, "location": location},
    )
    assert response.status_code == 201
    return cast(dict[str, object], response.json())


def _create_seat(
    client: TestClient,
    classroom_id: str,
    *,
    code: str = "S01",
    label: str = "좌석 1",
    geometry: dict[str, float] | None = None,
    row: int | None = None,
    column: int | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {"code": code, "label": label}
    if geometry is not None:
        payload["geometry"] = geometry
    if row is not None:
        payload["row"] = row
    if column is not None:
        payload["column"] = column
    response = client.post(
        f"/api/v1/classrooms/{classroom_id}/seats",
        json=payload,
    )
    assert response.status_code == 201
    return cast(dict[str, object], response.json())


def _create_student(
    *,
    student_no: str = "20240001",
    name: str = "김철수",
) -> StudentIdentity:
    """시드된 학생 identity를 돌려준다.

    좌석-학생 지정 화면은 중립 조회 seam(StudentLookupPort)으로 학생을 조회하므로
    CRUD API(`POST /api/v1/students`, TASK-004 제거 대상)에 의존하지 않는다.
    """
    identity = _SEEDED_STUDENTS[student_no]
    assert identity.name == name
    return identity


# --- 강의실 화면 --------------------------------------------------------------


def test_create_page_renders_form(client: TestClient) -> None:
    """강의실 생성 화면은 POST /api/v1/classrooms로 제출하는 폼을 렌더링한다."""
    response = client.get("/classrooms/create")
    assert response.status_code == 200
    assert "강의실 등록" in response.text
    assert 'data-api-url="/api/v1/classrooms"' in response.text
    assert 'data-api-method="POST"' in response.text
    assert 'name="code"' in response.text
    assert 'name="name"' in response.text
    assert 'name="location"' in response.text


def test_edit_page_renders_prefilled_values(client: TestClient) -> None:
    """강의실 수정 화면은 기존 값과 PUT API 배선을 렌더링한다."""
    classroom = _create_classroom(client, code="B101", name="실습실", location="B동 1층")
    classroom_id = str(classroom["id"])

    response = client.get(f"/classrooms/{classroom_id}/edit")

    assert response.status_code == 200
    assert "강의실 수정" in response.text
    assert f'data-api-url="/api/v1/classrooms/{classroom_id}"' in response.text
    assert 'data-api-method="PUT"' in response.text
    assert 'value="B101"' in response.text
    assert 'value="실습실"' in response.text
    assert 'value="B동 1층"' in response.text
    assert 'name="is_active"' in response.text


def test_edit_page_redirects_when_classroom_missing(client: TestClient) -> None:
    """없는 강의실 수정 화면은 목록으로 리다이렉트한다."""
    response = client.get("/classrooms/missing/edit", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/classrooms"


# --- 좌석 목록 화면 ------------------------------------------------------------


def test_seats_page_renders_list_and_map(client: TestClient) -> None:
    """좌석 목록 화면은 행·열 기준 배치도·목록·수정/삭제 배선을 렌더링한다."""
    classroom = _create_classroom(client)
    classroom_id = str(classroom["id"])
    seat = _create_seat(
        client,
        classroom_id,
        code="S01",
        label="좌석 1",
        row=1,
        column=1,
    )
    _create_seat(client, classroom_id, code="S02", label="좌석 2")
    seat_id = str(seat["id"])

    response = client.get(f"/classrooms/{classroom_id}/seats")

    assert response.status_code == 200
    assert "좌석 관리" in response.text
    assert "좌석 배치" in response.text
    assert "좌석 목록" in response.text
    assert "좌석 1" in response.text
    assert "좌석 2" in response.text
    # 행·열이 있는 좌석은 배치도에, 없는 좌석은 목록에만 표시된다
    assert "seat-map--grid" in response.text
    assert "1행 1열" in response.text
    assert "배치도 미설정" in response.text
    assert f'href="/classrooms/{classroom_id}/seats/{seat_id}/edit"' in response.text
    assert f'data-api-url="/api/v1/classrooms/{classroom_id}/seats/{seat_id}"' in response.text
    assert 'data-api-method="DELETE"' in response.text
    assert f'href="/classrooms/{classroom_id}/seats/create"' in response.text


def test_seats_page_renders_empty_state(client: TestClient) -> None:
    """빈 강의실도 조작 가능한 1x1 grid와 tray를 표시한다 (AC-001)."""
    classroom = _create_classroom(client)
    classroom_id = str(classroom["id"])

    response = client.get(f"/classrooms/{classroom_id}/seats")

    assert response.status_code == 200
    assert "등록된 좌석이 없습니다." in response.text
    # AC-001: 빈 강의실도 usable 1x1 grid가 렌더링된다
    assert "좌석 배치" in response.text
    assert "grid-template-columns: repeat(1," in response.text
    assert "grid-template-rows: repeat(1," in response.text
    assert 'id="seat-cell-1-1"' in response.text


def test_seats_page_renders_grid_with_empty_cells(client: TestClient) -> None:
    """배치도는 max_row x max_column CSS Grid로 렌더링되고 빈 셀이 구분된다."""
    classroom = _create_classroom(client)
    classroom_id = str(classroom["id"])
    seat = _create_seat(client, classroom_id, code="S01", label="좌석 1", row=1, column=1)
    seat_2 = _create_seat(client, classroom_id, code="S02", label="좌석 2", row=2, column=3)
    _create_seat(client, classroom_id, code="S03", label="좌석 3")
    seat_id = str(seat["id"])
    seat_2_id = str(seat_2["id"])

    response = client.get(f"/classrooms/{classroom_id}/seats")

    assert response.status_code == 200
    # max_row(2)·max_column(3)이 CSS Grid 크기로 전달된다
    assert "grid-template-columns: repeat(3," in response.text
    assert "grid-template-rows: repeat(2," in response.text
    # 행·열이 있는 좌석은 배치도에 배치된다
    assert "seat-map--grid" in response.text
    assert "grid-row: 2; grid-column: 3;" in response.text
    # 행·열이 없는 좌석은 배치도에 없고 목록에만 표시된다
    assert "좌석 3" in response.text
    # 빈 셀이 시각적으로 구분된다
    assert "seat-map__empty" in response.text
    # 배치된 셀·빈 셀·tray 좌석·unplace는 stable ID의 native button이다
    assert f'id="seat-cell-{seat_id}"' in response.text
    assert f'id="seat-cell-{seat_2_id}"' in response.text
    assert 'id="seat-cell-1-2"' in response.text
    assert "seat-tray" in response.text
    assert f'id="unplace-{seat_id}"' in response.text
    assert f'id="unplace-{seat_2_id}"' in response.text
    # 각 셀은 정확히 하나의 button을 가지며 nested button이 없다
    # (occupied 셀은 seat ID `seat-cell-{id}`, 빈 셀은 좌표 ID `seat-cell-{r}-{c}`)
    cell_buttons = re.findall(
        r'<button[^>]*id="seat-cell-[^"]*"[^>]*>(.*?)</button>', response.text, re.DOTALL
    )
    assert len(cell_buttons) == 2 * 3
    assert all("<button" not in inner for inner in cell_buttons)
    # idle 상태: positioned cell은 native enabled button이고 aria-disabled는 생략/false다
    # (다른 occupied target의 aria-disabled=true·handler block은 JS 선택 시 동작이다)
    assert f'<button type="button" id="seat-cell-{seat_id}"' in response.text
    assert 'aria-disabled="true"' not in response.text
    assert 'class="seat-map__cell' in response.text
    # tray 좌석은 native button이며 idle에서 aria-pressed=false로 시작한다
    assert '<button type="button" id="tray-seat-' in response.text
    assert 'aria-pressed="false"' in response.text
    # live region(polite)과 alert region(role="alert")이 존재한다
    assert 'id="seat-grid-status"' in response.text
    assert 'aria-live="polite"' in response.text
    assert 'id="seat-grid-alert"' in response.text
    assert 'role="alert"' in response.text


def test_seats_page_redirects_when_classroom_missing(client: TestClient) -> None:
    """없는 강의실 좌석 목록 화면은 목록으로 리다이렉트한다."""
    response = client.get("/classrooms/missing/seats", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/classrooms"


# --- 강의실 현황 화면 -----------------------------------------------------------


def test_classroom_list_page_shows_assigned_student_on_seat_map(
    client: TestClient,
) -> None:
    """강의실 현황 화면의 좌석 배치도는 지정 학생 이름을 표시한다 (UI-REQ-006)."""
    classroom = _create_classroom(client, code="R-L01", name="현황테스트 강의실")
    classroom_id = str(classroom["id"])
    assigned_seat = _create_seat(
        client,
        classroom_id,
        code="SL01",
        label="지정 좌석",
        geometry={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
    )
    _create_seat(
        client,
        classroom_id,
        code="SL02",
        label="빈 좌석",
        geometry={"x": 0.5, "y": 0.2, "width": 0.3, "height": 0.4},
    )
    student = _create_student(student_no="20240001", name="김철수")
    student_id = student.id

    assign = client.put(
        f"/api/v1/classrooms/{classroom_id}/seats/{assigned_seat['id']}/assignment",
        json={"student_id": student_id},
    )
    assert assign.status_code == 200

    response = client.get(f"/classrooms?classroom_id={classroom_id}")

    assert response.status_code == 200
    # 지정된 좌석 카드에는 학생 이름이 표시된다
    assert "지정 좌석" in response.text
    assert "김철수" in response.text
    assert 'class="assigned-student"' in response.text
    # 미지정 좌석 카드에는 "미지정"으로 표시된다
    assert "빈 좌석" in response.text
    assert 'class="no-assignment"' in response.text
    assert response.text.count("미지정") == 1


# --- 좌석 생성/수정 화면 -------------------------------------------------------


def test_seat_create_page_renders_form(client: TestClient) -> None:
    """좌석 추가 화면은 POST API를 렌더링하고 row/column typing 입력이 없다 (AC-008)."""
    classroom = _create_classroom(client)
    classroom_id = str(classroom["id"])

    response = client.get(f"/classrooms/{classroom_id}/seats/create")

    assert response.status_code == 200
    assert "좌석 추가" in response.text
    assert f'data-api-url="/api/v1/classrooms/{classroom_id}/seats"' in response.text
    assert 'data-api-method="POST"' in response.text
    assert 'name="code"' in response.text
    assert 'name="label"' in response.text
    # AC-008: create form에는 row/column typing input이 없다
    assert 'name="row"' not in response.text
    assert 'name="column"' not in response.text
    assert 'name="geometry_x"' not in response.text
    assert 'name="is_active"' not in response.text


def test_seat_edit_page_renders_prefilled_values(client: TestClient) -> None:
    """좌석 수정 화면은 기존 값과 PUT API 배선을 렌더링하고 row/column typing 입력이 없다 (AC-008)."""
    classroom = _create_classroom(client)
    classroom_id = str(classroom["id"])
    seat = _create_seat(
        client,
        classroom_id,
        row=2,
        column=3,
    )
    seat_id = str(seat["id"])

    response = client.get(f"/classrooms/{classroom_id}/seats/{seat_id}/edit")

    assert response.status_code == 200
    assert "좌석 1 수정" in response.text
    assert f'data-api-url="/api/v1/classrooms/{classroom_id}/seats/{seat_id}"' in response.text
    assert 'data-api-method="PUT"' in response.text
    assert 'value="S01"' in response.text
    assert 'value="좌석 1"' in response.text
    # AC-008: edit form에도 row/column typing input이 없다
    assert 'name="row"' not in response.text
    assert 'name="column"' not in response.text
    assert 'name="geometry_x"' not in response.text
    assert 'name="is_active"' in response.text


def test_seat_edit_page_redirects_when_seat_missing(client: TestClient) -> None:
    """없는 좌석 수정 화면은 좌석 목록으로 리다이렉트한다."""
    classroom = _create_classroom(client)
    classroom_id = str(classroom["id"])

    response = client.get(
        f"/classrooms/{classroom_id}/seats/missing/edit",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == f"/classrooms/{classroom_id}/seats"


def test_seat_edit_page_redirects_when_classroom_missing(client: TestClient) -> None:
    """없는 강의실의 좌석 수정 화면은 좌석 목록으로 리다이렉트한다."""
    response = client.get("/classrooms/missing/seats/missing/edit", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/classrooms/missing/seats"


def test_seat_create_page_redirects_when_classroom_missing(client: TestClient) -> None:
    """없는 강의실의 좌석 추가 화면은 목록으로 리다이렉트한다."""
    response = client.get("/classrooms/missing/seats/create", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/classrooms"


# --- 좌석-학생 지정 화면 -------------------------------------------------------


def test_seat_assignments_page_renders(client: TestClient) -> None:
    """좌석-학생 지정 페이지는 좌석 목록과 지정 폼 배선을 렌더링한다."""
    classroom = _create_classroom(client, code="R-A01", name="지정테스트 강의실")
    classroom_id = str(classroom["id"])
    seat = _create_seat(client, classroom_id, code="SA01", label="좌석 1")
    seat_id = str(seat["id"])

    response = client.get(f"/classrooms/{classroom_id}/seat-assignments")

    assert response.status_code == 200
    assert "좌석-학생 지정" in response.text
    assert "SA01" in response.text
    assert "좌석 1" in response.text
    # 미지정 좌석은 "미지정"으로 표시된다
    assert "미지정" in response.text
    # 지정 폼 배선: PUT + required select
    assignment_url = f"/api/v1/classrooms/{classroom_id}/seats/{seat_id}/assignment"
    assert f'data-api-url="{assignment_url}"' in response.text
    assert 'data-api-method="PUT"' in response.text
    assert 'name="student_id"' in response.text
    assert "required" in response.text
    # 에러 박스는 각 form 내부에 배치된다 (form 수와 일치, 실제 요소 기준)
    assert response.text.count(
        '<p class="form-error" role="alert" data-form-error'
    ) == response.text.count("data-api-url=")


def test_seat_assignments_page_shows_assigned_student(client: TestClient) -> None:
    """지정된 좌석에는 학생 이름·학번이 표시되고 해제 폼이 렌더링된다."""
    classroom = _create_classroom(client, code="R-B01", name="지정 현황 강의실")
    classroom_id = str(classroom["id"])
    seat = _create_seat(client, classroom_id, code="SB01", label="좌석 1")
    seat_id = str(seat["id"])
    student = _create_student(student_no="20240001", name="김철수")
    student_id = student.id

    assign = client.put(
        f"/api/v1/classrooms/{classroom_id}/seats/{seat_id}/assignment",
        json={"student_id": student_id},
    )
    assert assign.status_code == 200

    response = client.get(f"/classrooms/{classroom_id}/seat-assignments")

    assert response.status_code == 200
    assert "김철수" in response.text
    assert "20240001" in response.text
    # 이미 지정된 학생이 select에 선택된 상태로 표시된다
    assert f'value="{student_id}"' in response.text
    assert "selected" in response.text
    # 해제 폼 배선: DELETE
    assert 'data-api-method="DELETE"' in response.text
    # 지정된 좌석은 지정/해제 2개 폼이며 각각 에러 박스가 있다 (실제 요소 기준)
    assert response.text.count(
        '<p class="form-error" role="alert" data-form-error'
    ) == response.text.count("data-api-url=")


def test_seat_assignments_page_lists_only_active_students(client: TestClient) -> None:
    """학생 선택 select에는 활성 학생만 표시된다."""
    classroom = _create_classroom(client, code="R-C01", name="활성 학생 강의실")
    classroom_id = str(classroom["id"])
    _create_seat(client, classroom_id, code="SC01", label="좌석 1")
    _create_student(student_no="20240001", name="김철수")
    # 이영희(비활성)는 시드에 inactive로 주입되어 있고, 중립 조회 계약상
    # list_active는 active-only를 돌려준다. CRUD API의 비활성화 호출은 필요 없다.
    _create_student(student_no="20240002", name="이영희")

    response = client.get(f"/classrooms/{classroom_id}/seat-assignments")

    assert response.status_code == 200
    assert "김철수" in response.text
    assert "이영희" not in response.text


def test_seat_assignments_page_calls_list_active_once_with_max_limit(
    client: TestClient,
) -> None:
    """assignment page는 list_active(max, 0)을 정확히 1회 호출한다 (SPEC).

    화면은 학생 선택 드롭다운을 위해 페이지네이션 최대 크기로 활성 학생을
    한 번에 조회한다. offset은 0, limit은 page_size_max여야 한다.
    """
    recording = RecordingStudentLookup(
        InMemoryStudentLookup(identities=tuple(_SEEDED_STUDENTS.values()))
    )
    service = ClassroomService(
        InMemoryClassroomRepository(),
        student_lookup=recording,
        assignment_repository=InMemorySeatAssignmentRepository(),
        occupancy_confidence_threshold=0.5,
        clock=lambda: datetime(2026, 8, 13, 9, 0, tzinfo=UTC),
    )
    app.dependency_overrides[get_classroom_service] = lambda: service
    app.dependency_overrides[get_student_lookup] = lambda: recording
    try:
        classroom = _create_classroom(client, code="R-C02", name="단일 조회 강의실")
        classroom_id = str(classroom["id"])
        _create_seat(client, classroom_id, code="SC02", label="좌석 1")

        response = client.get(f"/classrooms/{classroom_id}/seat-assignments")

        assert response.status_code == 200
        assert len(recording.list_active_calls) == 1
        limit, offset = recording.list_active_calls[0]
        assert limit == get_settings().page_size_max
        assert offset == 0
    finally:
        app.dependency_overrides.clear()


def test_seat_assignments_page_renders_empty_state(client: TestClient) -> None:
    """좌석이 없으면 빈 상태 안내를 렌더링한다."""
    classroom = _create_classroom(client, code="R-D01", name="좌석 없음 강의실")
    classroom_id = str(classroom["id"])

    response = client.get(f"/classrooms/{classroom_id}/seat-assignments")

    assert response.status_code == 200
    assert "등록된 좌석이 없습니다." in response.text
    assert "좌석-학생 지정 목록" not in response.text


def test_seat_assignments_page_redirects_when_classroom_missing(client: TestClient) -> None:
    """없는 강의실의 좌석-학생 지정 화면은 목록으로 리다이렉트한다."""
    response = client.get(
        "/classrooms/nonexistent/seat-assignments",
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/classrooms"


# --- 학생 상태 조회 화면 -------------------------------------------------------


def test_student_states_page_renders(client: TestClient) -> None:
    """학생 상태 화면은 강의실 선택 드롭다운과 네비게이션 링크를 렌더링한다."""
    classroom = _create_classroom(client, code="R-S01", name="상태테스트 강의실")
    classroom_id = str(classroom["id"])

    response = client.get(f"/classrooms/{classroom_id}/student-states")

    assert response.status_code == 200
    assert "학생 상태" in response.text
    assert "상태테스트 강의실" in response.text
    # 강의실 선택 드롭다운에 현재 강의실이 선택된 상태로 표시된다
    assert 'name="classroom_id"' in response.text
    assert f'value="{classroom_id}"' in response.text
    assert "selected" in response.text
    # 네비게이션 "학생 현황" nav-group에 학생 상태·좌석-학생 지정 링크가 있다 (UI-REQ-011)
    assert "학생 현황" in response.text
    assert '/student-states"' in response.text
    assert '/seat-assignments"' in response.text


def test_student_states_page_renders_empty_state(client: TestClient) -> None:
    """탐지 결과가 없으면 "데이터 없음" 안내 문구를 렌더링한다 (UI-REQ-010)."""
    classroom = _create_classroom(client, code="R-S02", name="빈상태 강의실")
    classroom_id = str(classroom["id"])

    response = client.get(f"/classrooms/{classroom_id}/student-states")

    assert response.status_code == 200
    assert "학생 상태 데이터가 없습니다." in response.text
    assert "학생 상태 목록" not in response.text


def test_student_states_page_redirects_when_classroom_missing(client: TestClient) -> None:
    """없는 강의실의 학생 상태 화면은 목록으로 리다이렉트한다."""
    response = client.get(
        "/classrooms/nonexistent/student-states",
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["location"] == "/classrooms"


# --- 화면이 표시할 입력 검증 오류 ----------------------------------------------


def test_classroom_validation_error_has_displayable_message(client: TestClient) -> None:
    """강의실 생성 폼이 제출할 API의 검증 오류는 화면 표시용 message를 담는다."""
    invalid = client.post(
        "/api/v1/classrooms",
        json={"code": "   ", "name": "이름", "location": "위치"},
    )
    assert invalid.status_code == 422
    message = invalid.json()["error"]["message"]
    assert isinstance(message, str) and message

    duplicate = client.post(
        "/api/v1/classrooms",
        json={"code": "D101", "name": "중복", "location": "D동"},
    )
    assert duplicate.status_code == 201
    duplicate_code = client.post(
        "/api/v1/classrooms",
        json={"code": "d101", "name": "중복 강의실", "location": "D동"},
    )
    assert duplicate_code.status_code == 409
    message = duplicate_code.json()["error"]["message"]
    assert isinstance(message, str) and message


def test_seat_validation_error_has_displayable_message(client: TestClient) -> None:
    """좌석 생성 폼이 제출할 API의 검증 오류는 화면 표시용 message를 담는다."""
    classroom = _create_classroom(client)
    classroom_id = str(classroom["id"])

    invalid = client.post(
        f"/api/v1/classrooms/{classroom_id}/seats",
        json={
            "code": "S01",
            "label": "좌석",
            "geometry": {"x": 0.9, "y": 0.1, "width": 0.2, "height": 0.2},
        },
    )
    assert invalid.status_code == 422
    message = invalid.json()["error"]["message"]
    assert isinstance(message, str) and message

    duplicate = client.post(
        f"/api/v1/classrooms/{classroom_id}/seats",
        json={"code": "S01", "label": "좌석 1"},
    )
    assert duplicate.status_code == 201
    duplicate_code = client.post(
        f"/api/v1/classrooms/{classroom_id}/seats",
        json={"code": "s01", "label": "중복 좌석"},
    )
    assert duplicate_code.status_code == 409
    message = duplicate_code.json()["error"]["message"]
    assert isinstance(message, str) and message


# --- /any/ 리다이렉트 라우트 테스트 -------------------------------------------


def test_seat_assignments_any_redirects_to_first_classroom(client: TestClient) -> None:
    """강의실이 있을 때 /any/seat-assignments는 첫 번째 강의실로 리다이렉트한다."""
    classroom = _create_classroom(client, code="X101")
    classroom_id = str(classroom["id"])
    response = client.get("/classrooms/any/seat-assignments", follow_redirects=False)
    assert response.status_code == 302
    assert f"/classrooms/{classroom_id}/seat-assignments" in response.headers["location"]


def test_student_states_any_redirects_to_first_classroom(client: TestClient) -> None:
    """강의실이 있을 때 /any/student-states는 첫 번째 강의실로 리다이렉트한다."""
    classroom = _create_classroom(client, code="Y101")
    classroom_id = str(classroom["id"])
    response = client.get("/classrooms/any/student-states", follow_redirects=False)
    assert response.status_code == 302
    assert f"/classrooms/{classroom_id}/student-states" in response.headers["location"]


def test_seat_assignments_any_redirects_to_create_when_empty(client: TestClient) -> None:
    """강의실이 없을 때 /any/seat-assignments는 /classrooms/create로 리다이렉트한다."""
    response = client.get("/classrooms/any/seat-assignments", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/classrooms/create"


def test_student_states_any_redirects_to_create_when_empty(client: TestClient) -> None:
    """강의실이 없을 때 /any/student-states는 /classrooms/create로 리다이렉트한다."""
    response = client.get("/classrooms/any/student-states", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/classrooms/create"
