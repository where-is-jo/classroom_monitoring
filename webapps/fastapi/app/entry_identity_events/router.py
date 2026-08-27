"""입구 얼굴 관측 이벤트의 내부 적재·관리 조회 API."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import StreamingResponse

from ..shared.broadcaster import InMemoryBroadcaster
from ..shared.config import Settings
from ..shared.dependencies import (
    get_broadcaster,
    get_entry_identity_event_service,
    get_settings,
)
from .models import EntryIdentityStatus
from .schemas import (
    EntryIdentityEventCreateRequest,
    EntryIdentityEventPageResponse,
    EntryIdentityEventResponse,
)
from .service import EntryIdentityEventService

internal_router = APIRouter(prefix="/internal", tags=["internal"])
api_router = APIRouter(prefix="/api/v1", tags=["entry-identity-events"])


@internal_router.post(
    "/entry-identity-events",
    response_model=EntryIdentityEventResponse,
    status_code=201,
)
def create_entry_identity_event(
    request: EntryIdentityEventCreateRequest,
    response: Response,
    service: EntryIdentityEventService = Depends(get_entry_identity_event_service),
) -> EntryIdentityEventResponse:
    result = service.save_event(
        event_id=request.event_id,
        camera_id=request.camera_id,
        captured_at=request.captured_at,
        sequence=request.sequence,
        frame=request.frame.to_domain(),
        processing_status=request.processing_status,
        observations=tuple(item.to_domain() for item in request.observations),
    )
    if not result.created:
        response.status_code = 200
    return EntryIdentityEventResponse.from_domain(result.event)


@internal_router.post(
    "/entry-identity-overlays",
    status_code=status.HTTP_202_ACCEPTED,
)
def publish_entry_identity_overlay(
    request: EntryIdentityEventCreateRequest,
    service: EntryIdentityEventService = Depends(get_entry_identity_event_service),
) -> Response:
    """얼굴 상자만 실시간으로 내보낸다. **저장하지 않는다.**

    저장 경로(`/internal/entry-identity-events`)와 본문 계약이 같다. 두 경로가 다른
    모양을 보내면 화면의 상자와 저장된 관측이 어긋날 수 있다.

    저장소를 건드리지 않으므로 응답이 빠르다. 만들어진 자원이 없어 201도 200도
    맞지 않아 202로 답한다.
    """
    service.publish_overlay(
        event_id=request.event_id,
        camera_id=request.camera_id,
        captured_at=request.captured_at,
        sequence=request.sequence,
        frame=request.frame.to_domain(),
        processing_status=request.processing_status,
        observations=tuple(item.to_domain() for item in request.observations),
    )
    return Response(status_code=status.HTTP_202_ACCEPTED)


@api_router.get("/video-streams/{stream_id}/entry-identity-events/stream")
async def stream_entry_identity_events(
    stream_id: str,
    service: EntryIdentityEventService = Depends(get_entry_identity_event_service),
    broadcaster: InMemoryBroadcaster = Depends(get_broadcaster),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """활성 입구 카메라의 저장 완료 얼굴 관측을 SSE로 전달한다."""
    camera_id = service.resolve_realtime_camera_id(stream_id)

    async def event_generator() -> AsyncIterator[str]:
        queue = broadcaster.subscribe()
        retry_milliseconds = settings.sse_reconnection_timeout_seconds * 1000
        try:
            yield f"retry: {retry_milliseconds}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(),
                        timeout=float(settings.sse_heartbeat_interval_seconds),
                    )
                    if (
                        isinstance(event, dict)
                        and event.get("type") == "entry-identity"
                        and event.get("camera_id") == camera_id
                    ):
                        yield f"id: {event.get('event_id', '')}\n"
                        yield "event: entry-identity\n"
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except TimeoutError:
                    yield ": heartbeat\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            broadcaster.unsubscribe(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@api_router.get(
    "/video-streams/{stream_id}/entry-identity-events",
    response_model=EntryIdentityEventPageResponse,
)
def list_entry_identity_events(
    stream_id: str,
    status: EntryIdentityStatus | None = None,
    student_id: str | None = None,
    from_at: Annotated[datetime | None, Query(alias="from")] = None,
    to_at: Annotated[datetime | None, Query(alias="to")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
    service: EntryIdentityEventService = Depends(get_entry_identity_event_service),
) -> EntryIdentityEventPageResponse:
    page = service.list_events(
        stream_id,
        status=status,
        student_id=student_id,
        from_at=from_at,
        to_at=to_at,
        limit=limit,
        cursor=cursor,
    )
    return EntryIdentityEventPageResponse.from_domain(page)
