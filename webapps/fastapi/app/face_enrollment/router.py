"""얼굴 등록 API, 페이지와 실시간 프레임 채널."""

from __future__ import annotations

from contextlib import suppress

from fastapi import APIRouter, Depends, Request, Response, WebSocket, WebSocketDisconnect, status

from ..face_embeddings.service import FaceEmbeddingService
from ..shared.dependencies import get_face_embedding_service, get_face_enrollment_service
from ..shared.templating import templates
from .errors import (
    EnrollmentConflictError,
    EnrollmentNotFoundError,
    FaceAnalyzerUnavailableError,
)
from .models import CreateEnrollmentCommand, EnrollmentStatus
from .schemas import (
    CreateEnrollmentRequest,
    EnrollmentResponse,
    FaceProfileResponse,
    FrameDecisionResponse,
)
from .service import FaceEnrollmentService

api_router = APIRouter(tags=["face-enrollments"])
page_router = APIRouter(tags=["face-enrollment-pages"])


@api_router.post(
    "/api/v1/students/{student_id}/face-enrollments",
    response_model=EnrollmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_enrollment(
    student_id: str,
    body: CreateEnrollmentRequest,
    response: Response,
    service: FaceEnrollmentService = Depends(get_face_enrollment_service),
) -> EnrollmentResponse:
    enrollment = service.create(
        CreateEnrollmentCommand(
            student_id=student_id,
            consent_confirmed=body.consent_confirmed,
            consent_confirmed_by=body.consent_confirmed_by,
        )
    )
    response.headers["Location"] = f"/api/v1/face-enrollments/{enrollment.id}"
    return EnrollmentResponse.from_domain(enrollment)


@api_router.get("/api/v1/face-enrollments/{enrollment_id}", response_model=EnrollmentResponse)
def get_enrollment(
    enrollment_id: str,
    service: FaceEnrollmentService = Depends(get_face_enrollment_service),
) -> EnrollmentResponse:
    return EnrollmentResponse.from_domain(service.get(enrollment_id))


@api_router.delete(
    "/api/v1/face-enrollments/{enrollment_id}", status_code=status.HTTP_204_NO_CONTENT
)
def abort_enrollment(
    enrollment_id: str,
    service: FaceEnrollmentService = Depends(get_face_enrollment_service),
) -> Response:
    service.abort(enrollment_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@api_router.get("/api/v1/students/{student_id}/face-profile", response_model=FaceProfileResponse)
def get_face_profile(
    student_id: str,
    service: FaceEnrollmentService = Depends(get_face_enrollment_service),
) -> FaceProfileResponse:
    return FaceProfileResponse.from_domain(student_id, service.get_profile(student_id))


@api_router.delete(
    "/api/v1/students/{student_id}/face-profile", status_code=status.HTTP_204_NO_CONTENT
)
def delete_face_profile(
    student_id: str,
    service: FaceEnrollmentService = Depends(get_face_enrollment_service),
    embedding_service: FaceEmbeddingService = Depends(get_face_embedding_service),
) -> Response:
    # 식별 갤러리의 근거부터 없앤다. 프로필만 지워 대표 embedding이 남으면
    # 사용자가 삭제한 뒤에도 다음 갤러리 갱신에서 다시 식별될 수 있다.
    embedding_service.delete_for_student(student_id)
    service.delete_profile(student_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@api_router.websocket("/api/v1/face-enrollments/{enrollment_id}/frames")
async def enrollment_frames(
    websocket: WebSocket,
    enrollment_id: str,
    service: FaceEnrollmentService = Depends(get_face_enrollment_service),
) -> None:
    await websocket.accept()
    disconnected = False
    try:
        while True:
            frame = await websocket.receive_bytes()
            decision = service.process_frame(enrollment_id, frame)
            await websocket.send_json(
                FrameDecisionResponse.from_domain(decision).model_dump(mode="json")
            )
            if decision.enrollment.status == EnrollmentStatus.COMPLETE:
                await websocket.close(code=1000)
                return
    except WebSocketDisconnect:
        disconnected = True
    except (
        EnrollmentNotFoundError,
        EnrollmentConflictError,
        FaceAnalyzerUnavailableError,
    ) as exc:
        await websocket.send_json({"error": {"code": exc.code, "message": exc.message}})
        await websocket.close(code=1008)
        with suppress(EnrollmentNotFoundError, EnrollmentConflictError):
            service.abort(enrollment_id)
    finally:
        if disconnected:
            with suppress(EnrollmentNotFoundError, EnrollmentConflictError):
                service.abort(enrollment_id)


@page_router.get("/students/{student_id}/face-enrollment")
def face_enrollment_page(request: Request, student_id: str) -> Response:
    return templates.TemplateResponse(
        request=request,
        name="face_enrollment/register.html",
        context={"student_id": student_id},
    )
