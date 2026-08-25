"""학생 등록 화면과 API."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status

from ..face_embeddings.service import FaceEmbeddingService
from ..shared.config import Settings
from ..shared.dependencies import get_face_embedding_service, get_settings, get_student_service
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
    embedding_service: FaceEmbeddingService = Depends(get_face_embedding_service),
    settings: Settings = Depends(get_settings),
) -> Response:
    students = service.list_students(limit=500)
    registered_student_ids = {
        model_name: embedding_service.registered_student_ids(model_name)
        for model_name in ("arcface", "adaface")
    }
    return templates.TemplateResponse(
        request=request,
        name="students/manage.html",
        context={
            "students": students,
            "active_face_model": settings.face_recognizer,
            "active_face_model_label": (
                "ArcFace" if settings.face_recognizer == "arcface" else "AdaFace"
            ),
            "face_embedding_student_ids": registered_student_ids,
        },
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
    settings: Settings = Depends(get_settings),
) -> StudentResponse:
    return StudentResponse.from_domain(
        service.create_for_student(
            student_id,
            payload.enrollment_id,
            expected_model_name=settings.face_recognizer,
        )
    )
