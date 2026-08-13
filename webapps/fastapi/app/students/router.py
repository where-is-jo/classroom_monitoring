"""학생 API와 페이지 라우터."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from ..shared.config import Settings
from ..shared.dependencies import get_settings, get_student_service
from ..shared.templating import templates
from .errors import StudentNotFoundError
from .schemas import (
    StudentCreateRequest,
    StudentListResponse,
    StudentResponse,
    StudentUpdateRequest,
)
from .service import StudentService

api_router = APIRouter(prefix="/api/v1/students", tags=["students"])
page_router = APIRouter(prefix="/students", tags=["student-pages"])


def _paging(limit: int | None, offset: int, settings: Settings) -> tuple[int, int]:
    return min(limit or settings.page_size_default, settings.page_size_max), offset


@api_router.post("", response_model=StudentResponse, status_code=status.HTTP_201_CREATED)
def create_student(
    payload: StudentCreateRequest,
    response: Response,
    service: StudentService = Depends(get_student_service),
) -> StudentResponse:
    student = service.create_student(
        student_no=payload.student_no,
        name=payload.name,
        department=payload.department,
    )
    response.headers["Location"] = f"/api/v1/students/{student.id}"
    return StudentResponse.from_domain(student)


@api_router.get("", response_model=StudentListResponse)
def list_students(
    limit: int | None = Query(default=None, ge=1),
    offset: int = Query(default=0, ge=0),
    is_active: bool | None = Query(default=None),
    service: StudentService = Depends(get_student_service),
    settings: Settings = Depends(get_settings),
) -> StudentListResponse:
    resolved_limit, resolved_offset = _paging(limit, offset, settings)
    return StudentListResponse.from_page(
        service.list_students(limit=resolved_limit, offset=resolved_offset, is_active=is_active),
        resolved_limit,
        resolved_offset,
    )


@api_router.get("/{student_id}", response_model=StudentResponse)
def get_student(
    student_id: str,
    service: StudentService = Depends(get_student_service),
) -> StudentResponse:
    return StudentResponse.from_domain(service.get_student(student_id))


@api_router.patch("/{student_id}", response_model=StudentResponse)
def update_student(
    student_id: str,
    payload: StudentUpdateRequest,
    service: StudentService = Depends(get_student_service),
) -> StudentResponse:
    student = service.update_student(
        student_id,
        name=payload.name,
        department=payload.department,
        is_active=payload.is_active,
    )
    return StudentResponse.from_domain(student)


@api_router.delete("/{student_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_student(
    student_id: str,
    service: StudentService = Depends(get_student_service),
) -> Response:
    service.deactivate_student(student_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@page_router.get("")
def students_list_page(
    request: Request,
    service: StudentService = Depends(get_student_service),
    settings: Settings = Depends(get_settings),
) -> Response:
    """학생 목록 페이지."""
    page = service.list_students(limit=settings.page_size_max, offset=0)
    return templates.TemplateResponse(
        request=request,
        name="students/list.html",
        context={"students": page.items},
    )


@page_router.get("/create")
def student_create_page(request: Request) -> Response:
    """학생 등록 페이지."""
    return templates.TemplateResponse(
        request=request,
        name="students/create.html",
        context={},
    )


@page_router.get("/{student_id}/edit")
def student_edit_page(
    student_id: str,
    request: Request,
    service: StudentService = Depends(get_student_service),
) -> Response:
    """학생 수정 페이지."""
    try:
        student = service.get_student(student_id)
    except StudentNotFoundError:
        return RedirectResponse(url="/students", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(
        request=request,
        name="students/edit.html",
        context={"student": student},
    )
