"""학생 등록 화면과 API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status

from ..face_embeddings.service import FaceEmbeddingService
from ..shared.dependencies import get_face_embedding_service, get_student_service
from ..shared.templating import templates
from .schemas import CreateStudentRequest, RegisterStudentFaceRequest, StudentResponse
from .service import StudentService

page_router = APIRouter(prefix="/students", tags=["student-pages"])
api_router = APIRouter(prefix="/api/v1/students", tags=["students"])


@page_router.get("")
@page_router.get("/new")
def student_registration_page(
    request: Request,
    service: StudentService = Depends(get_student_service),
) -> Response:
    return templates.TemplateResponse(
        request=request,
        name="students/manage.html",
        context={"students": service.list_students(limit=500)},
    )


@api_router.post("", response_model=StudentResponse, status_code=status.HTTP_201_CREATED)
def create_student(
    payload: CreateStudentRequest,
    response: Response,
    service: StudentService = Depends(get_student_service),
) -> StudentResponse:
    student = service.create(payload.to_command())
    response.headers["Location"] = f"/api/v1/students/{student.id}"
    return StudentResponse.from_domain(student)


@api_router.patch("/{student_id}/face-enrollment", response_model=StudentResponse)
def register_student_face(
    student_id: str,
    payload: RegisterStudentFaceRequest,
    service: FaceEmbeddingService = Depends(get_face_embedding_service),
) -> StudentResponse:
    return StudentResponse.from_domain(
        service.create_for_student(student_id, payload.enrollment_id)
    )
