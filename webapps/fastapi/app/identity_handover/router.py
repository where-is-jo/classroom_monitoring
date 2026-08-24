"""신원 인계 ROI 설정 페이지와 API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import Response

from ..shared.dependencies import get_identity_handover_route_service
from ..shared.templating import templates
from .schemas import (
    HandoverReferenceImageResponse,
    IdentityHandoverRouteListResponse,
    IdentityHandoverRouteResponse,
    SaveIdentityHandoverRouteRequest,
    WorkerIdentityHandoverRouteListResponse,
    WorkerIdentityHandoverRouteResponse,
)
from .service import IdentityHandoverRouteService

api_router = APIRouter(prefix="/api/v1", tags=["identity-handover"])
internal_router = APIRouter(prefix="/internal", tags=["internal"])
page_router = APIRouter()


@page_router.get("/identity-handover", include_in_schema=False)
def identity_handover_page(
    request: Request,
    classroom_id: Annotated[str | None, Query()] = None,
    service: IdentityHandoverRouteService = Depends(get_identity_handover_route_service),
) -> Response:
    classrooms = service.list_classrooms()
    selected = classroom_id or (classrooms[0].id if classrooms else None)
    classroom = service.get_classroom(selected) if selected else None
    options = service.page_options(selected) if selected else None
    return templates.TemplateResponse(
        request=request,
        name="identity_handover/index.html",
        context={
            "classrooms": classrooms,
            "classroom": classroom,
            "entry_cameras": options.entry_cameras if options else (),
            "classroom_cameras": options.classroom_cameras if options else (),
        },
    )


@api_router.get(
    "/classrooms/{classroom_id}/identity-handover-routes",
    response_model=IdentityHandoverRouteListResponse,
)
def list_identity_handover_routes(
    classroom_id: str,
    service: IdentityHandoverRouteService = Depends(get_identity_handover_route_service),
) -> IdentityHandoverRouteListResponse:
    return IdentityHandoverRouteListResponse(
        items=[
            IdentityHandoverRouteResponse.from_domain(route)
            for route in service.list_routes(classroom_id)
        ]
    )


@api_router.post(
    "/classrooms/{classroom_id}/identity-handover-reference-image/capture",
    response_model=HandoverReferenceImageResponse,
    status_code=status.HTTP_201_CREATED,
)
def capture_identity_handover_reference_image(
    classroom_id: str,
    camera_id: Annotated[str, Query(min_length=1, max_length=128)],
    service: IdentityHandoverRouteService = Depends(get_identity_handover_route_service),
) -> HandoverReferenceImageResponse:
    return HandoverReferenceImageResponse.from_domain(
        service.capture_reference_image(classroom_id, camera_id)
    )


@api_router.get(
    "/classrooms/{classroom_id}/identity-handover-reference-image",
)
def get_identity_handover_reference_image(
    classroom_id: str,
    camera_id: Annotated[str, Query(min_length=1, max_length=128)],
    service: IdentityHandoverRouteService = Depends(get_identity_handover_route_service),
) -> Response:
    image = service.get_reference_image(classroom_id, camera_id)
    return Response(
        content=image.content,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store"},
    )


@api_router.put(
    "/classrooms/{classroom_id}/identity-handover-routes/{classroom_camera_id}",
    response_model=IdentityHandoverRouteResponse,
)
def save_identity_handover_route(
    classroom_id: str,
    classroom_camera_id: str,
    payload: SaveIdentityHandoverRouteRequest,
    service: IdentityHandoverRouteService = Depends(get_identity_handover_route_service),
) -> IdentityHandoverRouteResponse:
    return IdentityHandoverRouteResponse.from_domain(
        service.save_route(payload.to_command(classroom_id, classroom_camera_id))
    )


@api_router.delete(
    "/classrooms/{classroom_id}/identity-handover-routes/{classroom_camera_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_identity_handover_route(
    classroom_id: str,
    classroom_camera_id: str,
    service: IdentityHandoverRouteService = Depends(get_identity_handover_route_service),
) -> Response:
    service.delete_route(classroom_id, classroom_camera_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@internal_router.get(
    "/identity-handover-routes",
    response_model=WorkerIdentityHandoverRouteListResponse,
)
def list_worker_identity_handover_routes(
    service: IdentityHandoverRouteService = Depends(get_identity_handover_route_service),
) -> WorkerIdentityHandoverRouteListResponse:
    return WorkerIdentityHandoverRouteListResponse(
        items=[
            WorkerIdentityHandoverRouteResponse.from_domain(route)
            for route in service.list_active_routes()
        ]
    )
