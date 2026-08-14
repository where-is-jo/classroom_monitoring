"""ROI 연결 페이지와 API."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, Response

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
    students = service.list_students()
    return templates.TemplateResponse(
        request=request,
        name="roi_connections/index.html",
        context={
            "classrooms": classrooms,
            "classroom": classroom,
            "seats": seats,
            "students": students,
        },
    )


# 실시간 영상 연결이 실패했을 때 ROI 화면이 대신 보여줄 이미지.
# 서비스 디렉터리 안(static/)에 둔다 — 이전에는 저장소 루트의 individual_tasks/를
# parents[4]로 거슬러 올라가 읽었는데, 그 디렉터리는 .gitignore 대상이고 컨테이너
# 이미지에도 들어가지 않아 clone·배포 어느 쪽에서도 파일이 없었다.
_FALLBACK_IMAGE_PATH = Path(__file__).resolve().parents[2] / "static" / "roi-fallback.jpg"


@page_router.get("/roi-connections/fallback-image", include_in_schema=False)
def roi_fallback_image() -> FileResponse:
    return FileResponse(
        _FALLBACK_IMAGE_PATH,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


@api_router.post(
    "/classrooms/{classroom_id}/roi-reference-image",
    response_model=ReferenceImageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_reference_image(
    classroom_id: str,
    image: Annotated[UploadFile, File()],
    service: RoiConnectionService = Depends(get_roi_connection_service),
) -> ReferenceImageResponse:
    content = await image.read(service.max_upload_bytes + 1)
    saved = service.save_reference_image(
        classroom_id,
        content_type=image.content_type,
        content=content,
        filename=image.filename,
    )
    return ReferenceImageResponse.from_domain(saved)


@api_router.get("/classrooms/{classroom_id}/roi-reference-image")
def get_reference_image(
    classroom_id: str,
    service: RoiConnectionService = Depends(get_roi_connection_service),
) -> Response:
    image = service.get_reference_image(classroom_id)
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
    service: RoiConnectionService = Depends(get_roi_connection_service),
) -> RoiConnectionListResponse:
    return RoiConnectionListResponse(
        items=[
            RoiConnectionResponse.from_domain(item)
            for item in service.list_connections(classroom_id)
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
