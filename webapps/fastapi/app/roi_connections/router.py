"""ROI 연결 페이지와 API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status
from fastapi.responses import Response

from ..shared.dependencies import get_roi_connection_service
from ..shared.templating import templates
from .schemas import (
    ApplyDetectionRoiRequest,
    ApplyDetectionRoiResponse,
    ConfirmAutoRoiRequest,
    ConfirmAutoRoiResponse,
    DetectionRoiPlanResponse,
    PlanDetectionRoiRequest,
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
    cameras = service.list_roi_camera_options(selected) if selected else []
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


@api_router.post(
    "/classrooms/{classroom_id}/roi-connections/auto/from-detections",
    response_model=DetectionRoiPlanResponse,
)
def plan_detection_roi_connections(
    classroom_id: str,
    payload: PlanDetectionRoiRequest,
    service: RoiConnectionService = Depends(get_roi_connection_service),
) -> DetectionRoiPlanResponse:
    """카메라가 실제로 본 탐지에서 좌석 자리를 찾는다. **저장하지 않는다.**

    좌석 격자를 사영하는 경로(결정 0039)와 달리 배치 정보를 쓰지 않고, 사람이 오래
    앉아 있던 자리를 밀도로 찾는다(결정 0041).

    **어느 자리가 어느 좌석인지는 응답에 없다.** 카메라는 자리를 알지만 좌석 이름을
    알지 못한다. 관리자가 화면에서 지정한 뒤 `.../from-detections/apply`로 저장한다.
    조회가 수천 건을 훑으므로 동기 endpoint로 두어 FastAPI가 threadpool에서 돌린다.
    """
    return DetectionRoiPlanResponse.from_domain(
        service.plan_detection_rois(payload.to_command(classroom_id))
    )


@api_router.post(
    "/classrooms/{classroom_id}/roi-connections/auto/from-detections/apply",
    response_model=ApplyDetectionRoiResponse,
)
def apply_detection_roi_connections(
    classroom_id: str,
    payload: ApplyDetectionRoiRequest,
    service: RoiConnectionService = Depends(get_roi_connection_service),
) -> ApplyDetectionRoiResponse:
    """관리자가 좌석을 지정한 자리를 ROI로 저장한다.

    좌표의 근거가 캡처 화면이 아니라 탐지 기록이라 `reference_image_revision`을 받지
    않는다. 확정 전까지 좌석 판정에 쓰이지 않는 것은 격자 경로와 같다.
    """
    return ApplyDetectionRoiResponse.from_domain(
        service.apply_detection_rois(payload.to_command(classroom_id))
    )


@api_router.post(
    "/classrooms/{classroom_id}/roi-connections/auto/confirm",
    response_model=ConfirmAutoRoiResponse,
)
def confirm_auto_roi_connections(
    classroom_id: str,
    payload: ConfirmAutoRoiRequest,
    service: RoiConnectionService = Depends(get_roi_connection_service),
) -> ConfirmAutoRoiResponse:
    """자동 생성한 ROI를 관리자가 확인했다고 표시해 좌석 판정에 넣는다."""
    return ConfirmAutoRoiResponse.from_domain(
        service.confirm_auto_connections(payload.to_command(classroom_id))
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


@api_router.delete(
    "/classrooms/{classroom_id}/seats/{seat_id}/roi-connection",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_roi_connection(
    classroom_id: str,
    seat_id: str,
    camera_id: Annotated[str, Query(min_length=1, max_length=128)],
    service: RoiConnectionService = Depends(get_roi_connection_service),
) -> Response:
    """좌석 하나의 ROI를 지운다.

    camera_id를 query로 받는 이유는 DELETE에 본문을 싣지 않기 위해서다. 같은 좌석이라도
    카메라마다 다른 ROI를 가지므로(결정 0019) 어느 화각의 것을 지울지 지정해야 한다.
    """
    service.delete_connection(classroom_id, camera_id, seat_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
