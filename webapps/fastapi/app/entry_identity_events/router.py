"""입구 얼굴 관측 이벤트의 내부 적재·관리 조회 API."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from ..shared.dependencies import get_entry_identity_event_service
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
