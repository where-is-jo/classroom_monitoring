"""학생 등록 프로토타입 화면."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ..shared.templating import templates

page_router = APIRouter(prefix="/students", tags=["student-pages"])


@page_router.get("/new")
def student_registration_page(request: Request) -> Response:
    return templates.TemplateResponse(
        request=request,
        name="students/register.html",
        context={},
    )
