"""ROI 연결 페이지와 API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from fastapi.responses import Response

from ..shared.dependencies import get_roi_connection_service
from ..shared.templating import templates
from .schemas import (
    ReferenceImageResponse,
    RoiConnectionListResponse,
    RoiConnectionResponse,
    SaveLiveRoiConnectionRequest,
    SaveRoiConnectionRequest,
)
from .service import RoiConnectionService

api_router = APIRouter(prefix="/api/v1", tags=["roi-connections"])
page_router = APIRouter()


@page_router.get("/roi-connections", include_in_schema=False)
def roi_connections_page(
    request: Request,
    classroom_id: Annotated[str | None, Query()] = None,
    service: RoiConnectionService = Depends(get_roi_connection_service),
) -> Response:
    classrooms = service.list_classrooms()
    selected = classroom_id or (classrooms[0].id if classrooms else None)
    classroom = service.get_classroom(selected) if selected else None
    seats = service.list_seats(selected) if selected else []
    cameras = service.list_camera_options(selected) if selected else []
    students = service.list_students()
    return templates.TemplateResponse(
        request=request,
        name="roi_connections/index.html",
        context={
            "classrooms": classrooms,
            "classroom": classroom,
            "seats": seats,
            "cameras": cameras,
            "students": students,
        },
    )


@api_router.post(
    "/classrooms/{classroom_id}/roi-reference-image",
    response_model=ReferenceImageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_reference_image(
    classroom_id: str,
    camera_id: Annotated[str, Query(min_length=1, max_length=128)],
    image: Annotated[UploadFile, File()],
    service: RoiConnectionService = Depends(get_roi_connection_service),
) -> ReferenceImageResponse:
    content = await image.read(service.max_upload_bytes + 1)
    saved = service.save_reference_image(
        classroom_id,
        camera_id,
        content_type=image.content_type,
        content=content,
        filename=image.filename,
    )
    return ReferenceImageResponse.from_domain(saved)


@api_router.post(
    "/classrooms/{classroom_id}/roi-reference-image/capture",
    response_model=ReferenceImageResponse,
    status_code=status.HTTP_201_CREATED,
)
def capture_reference_image(
    classroom_id: str,
    camera_id: Annotated[str, Query(min_length=1, max_length=128)],
    service: RoiConnectionService = Depends(get_roi_connection_service),
) -> ReferenceImageResponse:
    """카메라의 현재 화면을 잡아 ROI 기준 이미지로 저장한다.

    RTSP 연결과 디코딩이 있어 실측 4초대가 걸린다. 동기 endpoint라 FastAPI가
    threadpool에서 실행하므로 다른 요청을 막지는 않는다.
    """
    return ReferenceImageResponse.from_domain(
        service.capture_reference_image(classroom_id, camera_id)
    )


@api_router.get("/classrooms/{classroom_id}/roi-reference-image")
def get_reference_image(
    classroom_id: str,
    camera_id: Annotated[str, Query(min_length=1, max_length=128)],
    service: RoiConnectionService = Depends(get_roi_connection_service),
) -> Response:
    image = service.get_reference_image(classroom_id, camera_id)
    return Response(
        content=image.content,
        media_type=image.content_type,
        headers={"Cache-Control": "no-store"},
    )


@api_router.get(
    "/classrooms/{classroom_id}/roi-connections",
    response_model=RoiConnectionListResponse,
)
def list_roi_connections(
    classroom_id: str,
    camera_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    service: RoiConnectionService = Depends(get_roi_connection_service),
) -> RoiConnectionListResponse:
    return RoiConnectionListResponse(
        items=[
            RoiConnectionResponse.from_domain(item)
            for item in service.list_connections(classroom_id, camera_id)
        ]
    )


@api_router.put(
    "/classrooms/{classroom_id}/seats/{seat_id}/roi-connection",
    response_model=RoiConnectionResponse,
)
def save_roi_connection(
    classroom_id: str,
    seat_id: str,
    payload: SaveRoiConnectionRequest,
    service: RoiConnectionService = Depends(get_roi_connection_service),
) -> RoiConnectionResponse:
    return RoiConnectionResponse.from_domain(
        service.save_connection(payload.to_command(classroom_id, seat_id))
    )


@api_router.put(
    "/classrooms/{classroom_id}/roi-connection",
    response_model=RoiConnectionResponse,
)
def save_live_roi_connection(
    classroom_id: str,
    payload: SaveLiveRoiConnectionRequest,
    service: RoiConnectionService = Depends(get_roi_connection_service),
) -> RoiConnectionResponse:
    return RoiConnectionResponse.from_domain(
        service.save_live_connection(payload.to_command(classroom_id))
    )
