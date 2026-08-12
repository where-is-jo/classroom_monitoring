"""탐지 스냅샷 조회 라우터.

같은 서비스 함수를 화면(page_router)과 API(api_router)가 함께 쓴다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from ..shared.dependencies import get_snapshot_service
from ..shared.templating import templates
from .errors import SnapshotStorageUnavailableError
from .schemas import SnapshotListResponse
from .service import SnapshotService

api_router = APIRouter(prefix="/api/v1", tags=["snapshots"])
page_router = APIRouter(tags=["snapshot-pages"])


@api_router.get("/snapshots", response_model=SnapshotListResponse)
def list_snapshots(
    camera_id: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    service: SnapshotService = Depends(get_snapshot_service),
) -> SnapshotListResponse:
    page = service.list_snapshots(camera_id=camera_id, limit=limit, offset=offset)
    return SnapshotListResponse.from_domain(page)


# 키에 슬래시가 들어 있어 path 변환자를 쓴다(camera-01/2026-08-12/....jpg).
@api_router.get("/snapshots/image/{key:path}")
def get_snapshot_image(
    key: str,
    service: SnapshotService = Depends(get_snapshot_service),
) -> Response:
    """이미지 바이트를 fastapi가 대신 전달한다.

    presigned URL로 브라우저를 MinIO에 직접 붙이지 않는다. "브라우저는 fastapi만
    호출한다"는 아키텍처 규칙 때문이다.
    """
    content = service.get_image(key)
    return Response(
        content=content.data,
        media_type=content.content_type,
        # 객체 키에 촬영 시각이 들어 있어 같은 키의 내용은 바뀌지 않는다.
        headers={"Cache-Control": "private, max-age=3600"},
    )


@page_router.get("/snapshots")
def snapshots_page(
    request: Request,
    camera_id: str | None = Query(default=None, max_length=128),
    service: SnapshotService = Depends(get_snapshot_service),
) -> Response:
    # "스냅샷이 없다"와 "저장소 조회 실패"를 화면에서 구분한다. 둘을 같은 빈 화면으로
    # 보여주면 카메라 문제인지 저장소 문제인지 운영자가 알 수 없다.
    storage_error = False
    page = None
    camera_options: list[str] = []
    try:
        page = service.list_snapshots(camera_id=camera_id, limit=50)
        camera_options = service.camera_options()
    except SnapshotStorageUnavailableError:
        storage_error = True

    return templates.TemplateResponse(
        request=request,
        name="snapshots/snapshots.html",
        context={
            "snapshots": page.items if page is not None else [],
            "total": page.total if page is not None else 0,
            "camera_options": camera_options,
            "selected_camera_id": camera_id or "",
            "storage_error": storage_error,
        },
    )
