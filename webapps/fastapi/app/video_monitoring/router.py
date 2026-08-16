"""Router for monitoring queries and rule-based search without authentication."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request, Response

from ..shared.config import Settings
from ..shared.dependencies import (
    get_playback_session_service,
    get_settings,
    get_video_demo_service,
    get_video_stream_service,
)
from ..shared.templating import templates
from .errors import DemoStreamNotFoundError, PlaybackSessionInputError, VideoSearchInputError
from .models import DemoStreamStatus
from .schemas import (
    DemoStreamResponse,
    PlaybackSessionCreateResponse,
    RealStreamResponse,
    StreamListResponse,
    VideoSearchRequest,
    VideoSearchResponse,
)
from .service import PlaybackSessionService, VideoDemoService, VideoStreamService

api_router = APIRouter(prefix="/api/v1", tags=["monitoring"])
page_router = APIRouter(tags=["monitoring-pages"])

# 결정 0014의 HttpOnly owner cookie 이름은 session마다 별도로 둔다.
# 한 화면에 여러 카드가 동시에 활성화되므로 단일 이름 cookie로는 여러 세션을
# 구분할 수 없다.
_PLAYBACK_OWNER_COOKIE_PREFIX = "playback_owner_"


@api_router.get("/video-streams", response_model=StreamListResponse)
def list_video_streams(
    q: str | None = Query(default=None, max_length=100),
    classroom_id: str | None = Query(default=None, max_length=128),
    stream_status: DemoStreamStatus | None = Query(default=None, alias="status"),
    demo_service: VideoDemoService = Depends(get_video_demo_service),
    stream_service: VideoStreamService = Depends(get_video_stream_service),
) -> StreamListResponse:
    items: list[DemoStreamResponse | RealStreamResponse] = []

    # Add demo streams
    demo_streams = demo_service.list_streams(
        search=q, classroom_id=classroom_id, status=stream_status
    )
    items.extend(DemoStreamResponse.from_domain(item) for item in demo_streams)

    # Add real streams
    real_streams = stream_service.list_streams()
    for stream in real_streams:
        if classroom_id and stream.classroom_id != classroom_id:
            continue
        status = stream_service.get_source_status(stream)
        items.append(RealStreamResponse.from_domain(stream, status))

    return StreamListResponse(items=items, total=len(items))


@api_router.get(
    "/video-streams/{stream_id}", response_model=DemoStreamResponse | RealStreamResponse
)
def get_video_stream(
    stream_id: str,
    demo_service: VideoDemoService = Depends(get_video_demo_service),
    stream_service: VideoStreamService = Depends(get_video_stream_service),
) -> DemoStreamResponse | RealStreamResponse:
    # demo catalog를 먼저 본다. 없으면 실제 source에서 찾는다.
    # 여기서 삼키는 것은 "demo에 없다"는 사실 하나뿐이다. 실제 source 조회의
    # 실패는 그대로 올라가야 하므로 넓은 except로 감싸지 않는다.
    try:
        return DemoStreamResponse.from_domain(demo_service.get_stream(stream_id))
    except DemoStreamNotFoundError:
        pass

    stream = stream_service.get_stream(stream_id)
    status = stream_service.get_source_status(stream)
    return RealStreamResponse.from_domain(stream, status)


@api_router.post("/video-searches", response_model=VideoSearchResponse)
def search_videos(
    payload: VideoSearchRequest,
    service: VideoDemoService = Depends(get_video_demo_service),
) -> VideoSearchResponse:
    return VideoSearchResponse.from_domain(
        service.search_videos(
            payload.query,
            classroom_id=payload.classroom_id,
            from_at=payload.from_at,
            to_at=payload.to_at,
            limit=payload.limit,
        )
    )


@page_router.get("/monitoring")
def monitoring_page(
    request: Request,
    stream_service: VideoStreamService = Depends(get_video_stream_service),
) -> Response:
    """연결된 실제 카메라만 보여주는 실시간 모니터링 화면 (MON-001~006)."""
    real_streams = stream_service.list_monitoring_streams()
    return templates.TemplateResponse(
        request=request,
        name="video_monitoring/monitoring.html",
        context={
            "real_streams": real_streams,
            "stream_service": stream_service,
            "current_time": datetime.now(UTC),
        },
    )


@page_router.get("/video-search")
def video_search_page(
    request: Request,
    query: str | None = Query(default=None, min_length=1, max_length=200),
    classroom_id: str | None = Query(default=None, max_length=128),
    from_at: str | None = Query(default=None, alias="from", max_length=40),
    to_at: str | None = Query(default=None, alias="to", max_length=40),
    limit: int = Query(default=20, ge=1, le=50),
    service: VideoDemoService = Depends(get_video_demo_service),
) -> Response:
    parsed_from = _page_datetime(from_at)
    parsed_to = _page_datetime(to_at)
    results = None
    if query is not None:
        results = service.search_videos(
            query,
            classroom_id=classroom_id,
            from_at=parsed_from,
            to_at=parsed_to,
            limit=limit,
        )
    options = service.classroom_options()
    return templates.TemplateResponse(
        request=request,
        name="video_monitoring/search.html",
        context={
            "results": results,
            "classroom_options": options,
            "selected_query": query or "",
            "selected_classroom_id": classroom_id or "",
            "selected_from": parsed_from,
            "selected_to": parsed_to,
            "selected_limit": limit,
            "demo_enabled": bool(options),
        },
    )


def _page_datetime(value: str | None) -> datetime | None:
    if value is None or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise VideoSearchInputError("Invalid datetime format.") from None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=ZoneInfo("Asia/Seoul"))
    return parsed


# ── playback session (결정 0014) ──────────────────────────────────────────────


def _playback_owner_cookie_name(session_id: str) -> str:
    return f"{_PLAYBACK_OWNER_COOKIE_PREFIX}{session_id}"


def _signaling_url(stream_id: str, session_id: str) -> str:
    return f"/api/v1/video-streams/{stream_id}/playback-sessions/{session_id}"


@api_router.post(
    "/video-streams/{stream_id}/playback-sessions",
    response_model=PlaybackSessionCreateResponse,
    status_code=201,
)
def create_playback_session(
    stream_id: str,
    response: Response,
    service: PlaybackSessionService = Depends(get_playback_session_service),
    settings: Settings = Depends(get_settings),
) -> PlaybackSessionCreateResponse:
    """실제·enabled·WebRTC source의 짧은 수명 재생 세션을 만든다.

    응답은 opaque session_id, FastAPI signaling URL, expires_at만 포함하고,
    owner cookie(HttpOnly·Secure·SameSite=Strict)를 함께 설정한다.
    """
    created = service.create_session(stream_id)
    session = created.session
    signaling_url = _signaling_url(stream_id, session.session_id)
    response.set_cookie(
        key=_playback_owner_cookie_name(session.session_id),
        value=created.owner_token,
        httponly=True,
        secure=settings.playback_session_cookie_secure,
        samesite="strict",
        max_age=settings.playback_session_ttl_seconds,
        path="/",
    )
    response.headers["Location"] = signaling_url
    return PlaybackSessionCreateResponse.from_domain(session, signaling_url)


@api_router.post("/video-streams/{stream_id}/playback-sessions/{session_id}")
async def whep_offer(
    stream_id: str,
    session_id: str,
    request: Request,
    service: PlaybackSessionService = Depends(get_playback_session_service),
    settings: Settings = Depends(get_settings),
) -> Response:
    """WHEP offer(SDP)를 MediaMTX에 대행하고 answer를 돌려준다 (201)."""
    offer_sdp = await _read_sdp_body(request, settings.playback_session_sdp_max_bytes)
    owner_token = request.cookies.get(_playback_owner_cookie_name(session_id))
    answer_sdp = service.activate(
        session_id=session_id,
        stream_id=stream_id,
        owner_token=owner_token,
        offer_sdp=offer_sdp,
    )
    return Response(content=answer_sdp, media_type="application/sdp", status_code=201)


@api_router.patch("/video-streams/{stream_id}/playback-sessions/{session_id}")
async def whep_renegotiate(
    stream_id: str,
    session_id: str,
    request: Request,
    service: PlaybackSessionService = Depends(get_playback_session_service),
    settings: Settings = Depends(get_settings),
) -> Response:
    """ACTIVE 세션의 재협상(SDP)을 MediaMTX에 대행한다."""
    offer_sdp = await _read_sdp_body(request, settings.playback_session_sdp_max_bytes)
    owner_token = request.cookies.get(_playback_owner_cookie_name(session_id))
    answer_sdp = service.renegotiate(
        session_id=session_id,
        stream_id=stream_id,
        owner_token=owner_token,
        offer_sdp=offer_sdp,
    )
    return Response(content=answer_sdp, media_type="application/sdp", status_code=200)


@api_router.delete(
    "/video-streams/{stream_id}/playback-sessions/{session_id}",
    status_code=204,
)
def whep_close(
    stream_id: str,
    session_id: str,
    request: Request,
    service: PlaybackSessionService = Depends(get_playback_session_service),
) -> Response:
    """WHEP resource를 닫고 세션을 CLOSED로 만든다. CLOSED에서는 idempotent.

    owner cookie는 TTL(Max-Age)로 자동 만료된다. 응답에서 즉시 삭제하지 않는
    이유는 DELETE의 idempotent 재시도(결정 0014)를 위해 cookie가 유지돼야 하기
    때문이다.
    """
    owner_token = request.cookies.get(_playback_owner_cookie_name(session_id))
    service.close(
        session_id=session_id,
        stream_id=stream_id,
        owner_token=owner_token,
    )
    return Response(status_code=204)


async def _read_sdp_body(request: Request, max_bytes: int) -> str:
    body = await request.body()
    if len(body) > max_bytes:
        raise PlaybackSessionInputError("SDP 본문이 허용 크기를 초과했습니다.")
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        raise PlaybackSessionInputError("SDP 본문을 UTF-8로 해석할 수 없습니다.") from None
