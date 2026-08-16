"""Student monitoring router."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, StreamingResponse

from ..shared.broadcaster import InMemoryBroadcaster
from ..shared.config import Settings
from ..shared.dependencies import (
    get_broadcaster,
    get_detection_event_repository,
    get_settings,
    get_student_monitoring_service,
    get_video_segment_repository,
    get_video_stream_repository,
    utc_now,
)
from ..video_monitoring.errors import VideoStreamNotFoundError
from ..video_monitoring.ports import VideoStreamRepository
from .models import (
    Detection,
    DetectionEvent,
    DetectionEventPage,
    FrameInfo,
    VideoSegment,
)
from .ports import DetectionEventRepository, VideoSegmentRepository
from .schemas import (
    DetectionEventListResponse,
    DetectionEventResponse,
    InferenceEventRequest,
    InferenceEventResponse,
    StudentSeatStateResponse,
    StudentStateListResponse,
    VideoSegmentDetailResponse,
    VideoSegmentListResponse,
    VideoSegmentRequest,
    VideoSegmentResponse,
)
from .service import StudentMonitoringService

internal_router = APIRouter(prefix="/internal", tags=["student-monitoring-internal"])
api_router = APIRouter(prefix="/api/v1", tags=["student-monitoring"])


@internal_router.post("/inference/events", response_model=InferenceEventResponse)
def receive_inference_event(
    request: InferenceEventRequest,
    service: StudentMonitoringService = Depends(get_student_monitoring_service),
) -> InferenceEventResponse | JSONResponse:
    """Receive inference event from worker."""
    event = DetectionEvent(
        event_id=request.event_id,
        camera_id=request.camera_id,
        stream_id="",
        classroom_id="",
        captured_at=request.captured_at,
        sequence=request.sequence,
        frame=FrameInfo(
            width_pixels=request.frame.width_pixels,
            height_pixels=request.frame.height_pixels,
        ),
        detections=tuple(
            Detection(
                detection_id=d.detection_id,
                class_id=d.class_id,
                class_name=d.class_name,
                confidence=d.confidence,
                bbox=d.bbox,
                student_id=d.student_id,
                identity_confidence=d.identity_confidence,
                face_bbox=d.face_bbox,
            )
            for d in request.detections
        ),
        received_at=utc_now(),
        schema_version=1,
    )

    result = service.receive_inference_event(event)

    response_data = InferenceEventResponse(
        event_id=result.event.event_id,
        received_at=result.event.received_at,
    )

    if result.is_new:
        return JSONResponse(status_code=201, content=response_data.model_dump(mode="json"))
    return JSONResponse(status_code=200, content=response_data.model_dump(mode="json"))


@internal_router.post("/video-segments", response_model=VideoSegmentResponse, status_code=201)
def receive_video_segment(
    request: VideoSegmentRequest,
    service: StudentMonitoringService = Depends(get_student_monitoring_service),
) -> VideoSegmentResponse:
    """Receive video segment from worker."""
    segment = VideoSegment(
        segment_id=request.segment_id,
        camera_id=request.camera_id,
        stream_id="",
        classroom_id="",
        recorded_from=request.recorded_from,
        recorded_to=request.recorded_to,
        storage=request.storage,
        bucket_alias=request.bucket_alias,
        object_key=request.object_key,
        size_bytes=request.size_bytes,
        received_at=utc_now(),
        schema_version=1,
    )

    saved = service.receive_video_segment(segment)

    return VideoSegmentResponse(
        segment_id=saved.segment_id,
        received_at=saved.received_at,
    )


@api_router.get(
    "/video-streams/{stream_id}/detections",
    response_model=DetectionEventListResponse,
)
def list_detections(
    stream_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    from_at: datetime | None = Query(default=None, alias="from"),
    to_at: datetime | None = Query(default=None, alias="to"),
    stream_repository: VideoStreamRepository = Depends(get_video_stream_repository),
    detection_repository: DetectionEventRepository = Depends(get_detection_event_repository),
) -> DetectionEventListResponse:
    """List detection events for a video stream."""
    stream = stream_repository.find_by_id(stream_id) or stream_repository.find_by_camera_id(
        stream_id
    )
    if stream is None:
        raise VideoStreamNotFoundError()

    if from_at is not None and to_at is not None:
        page = detection_repository.find_by_camera_and_period(
            camera_id=stream.camera_id,
            from_dt=from_at,
            to_dt=to_at,
            limit=limit,
            cursor=cursor,
        )
    else:
        events = detection_repository.find_recent_by_camera(
            camera_id=stream.camera_id,
            limit=limit,
        )
        page = DetectionEventPage(items=events, total=len(events), next_cursor=None)

    return DetectionEventListResponse(
        items=[DetectionEventResponse.from_domain(e) for e in page.items],
        total=page.total,
        next_cursor=page.next_cursor,
    )


@api_router.get(
    "/video-segments",
    response_model=VideoSegmentListResponse,
)
def list_video_segments(
    camera_id: str = Query(...),
    from_at: datetime = Query(..., alias="from"),
    to_at: datetime = Query(..., alias="to"),
    limit: int = Query(default=50, ge=1, le=200),
    segment_repository: VideoSegmentRepository = Depends(get_video_segment_repository),
) -> VideoSegmentListResponse:
    """List video segments by camera and period."""
    segments = segment_repository.find_by_camera_and_period(
        camera_id=camera_id,
        from_dt=from_at,
        to_dt=to_at,
        limit=limit,
    )

    return VideoSegmentListResponse(
        items=[VideoSegmentDetailResponse.from_domain(s) for s in segments],
        total=len(segments),
    )


@api_router.get("/video-streams/{stream_id}/detection-events")
async def stream_detection_events(
    stream_id: str,
    stream_repository: VideoStreamRepository = Depends(get_video_stream_repository),
    broadcaster: InMemoryBroadcaster = Depends(get_broadcaster),
) -> StreamingResponse:
    """SSE endpoint for real-time detection events."""
    stream = stream_repository.find_by_id(stream_id) or stream_repository.find_by_camera_id(
        stream_id
    )
    if stream is None:
        raise VideoStreamNotFoundError()

    async def event_generator() -> AsyncIterator[str]:
        queue = broadcaster.subscribe()
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    if event.get("camera_id") == stream.camera_id:
                        yield f"id: {event.get('event_id', '')}\n"
                        yield "event: detection\n"
                        yield f"data: {json.dumps(event)}\n\n"
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
    "/classrooms/{classroom_id}/student-states",
    response_model=StudentStateListResponse,
)
def list_student_states(
    classroom_id: str,
    service: StudentMonitoringService = Depends(get_student_monitoring_service),
) -> StudentStateListResponse:
    """강의실의 학생별 현재 상태를 반환한다."""
    states = service.list_student_states(classroom_id)
    return StudentStateListResponse(
        classroom_id=classroom_id,
        states=[
            StudentSeatStateResponse(
                student_id=s.student_id,
                student_name=s.student_name,
                student_no=s.student_no,
                assigned_seat_id=s.assigned_seat_id,
                assigned_seat_label=s.assigned_seat_label,
                current_state=s.current_state.value,
                confidence=s.confidence,
                last_observed_at=s.last_observed_at,
            )
            for s in states
        ],
    )


@api_router.get("/classrooms/{classroom_id}/student-state-events")
async def stream_student_state_events(
    classroom_id: str,
    service: StudentMonitoringService = Depends(get_student_monitoring_service),
    broadcaster: InMemoryBroadcaster = Depends(get_broadcaster),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """강의실 학생 상태 변경을 단일 프로세스 SSE로 전달한다."""
    service.list_student_states(classroom_id)

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
                        and event.get("type") == "student-state"
                        and event.get("classroom_id") == classroom_id
                    ):
                        yield f"id: {event.get('event_id', '')}\n"
                        yield "event: student-state\n"
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
