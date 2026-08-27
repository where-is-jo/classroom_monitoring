"""Router for monitoring queries and rule-based search without authentication."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, Request, Response

from ..shared.config import Settings
from ..shared.dependencies import (
    get_playback_session_service,
    get_settings,
    get_video_stream_service,
)
from ..shared.templating import templates
from .errors import PlaybackSessionInputError
from .schemas import (
    PlaybackSessionCreateResponse,
    RealStreamResponse,
    StreamListResponse,
    VideoStreamCreateRequest,
)
from .service import PlaybackSessionService, VideoStreamService

api_router = APIRouter(prefix="/api/v1", tags=["monitoring"])
page_router = APIRouter(tags=["monitoring-pages"])

# 결정 0014의 HttpOnly owner cookie 이름은 session마다 별도로 둔다.
# 한 화면에 여러 카드가 동시에 활성화되므로 단일 이름 cookie로는 여러 세션을
# 구분할 수 없다.
_PLAYBACK_OWNER_COOKIE_PREFIX = "playback_owner_"


@api_router.get("/video-streams", response_model=StreamListResponse)
def list_video_streams(
    classroom_id: str | None = Query(default=None, max_length=128),
    stream_service: VideoStreamService = Depends(get_video_stream_service),
) -> StreamListResponse:
    """등록된 실제 카메라 source를 돌려준다."""
    items: list[RealStreamResponse] = []
    for stream in stream_service.list_streams():
        if classroom_id and stream.classroom_id != classroom_id:
            continue
        status = stream_service.get_source_status(stream)
        items.append(RealStreamResponse.from_domain(stream, status))

    return StreamListResponse(items=items, total=len(items))


@api_router.post("/video-streams", response_model=RealStreamResponse, status_code=201)
def create_video_stream(
    request: VideoStreamCreateRequest,
    stream_service: VideoStreamService = Depends(get_video_stream_service),
) -> RealStreamResponse:
    """실제 카메라 source를 등록한다 (201).

    MongoDB mode에는 demo seed가 돌지 않으므로 이 경로로 camera_id를 원장에 넣어야
    worker의 탐지 이벤트가 받아들여진다. 등록 직후에는 아직 프레임이 없으므로
    상태는 UNKNOWN이다.
    """
    stream = stream_service.register_stream(
        camera_id=request.camera_id,
        classroom_id=request.classroom_id,
        camera_label=request.camera_label,
        enabled=request.enabled,
        role=request.role,
    )
    return RealStreamResponse.from_domain(stream, stream_service.get_source_status(stream))


@api_router.get("/video-streams/{stream_id}", response_model=RealStreamResponse)
def get_video_stream(
    stream_id: str,
    stream_service: VideoStreamService = Depends(get_video_stream_service),
) -> RealStreamResponse:
    stream = stream_service.get_stream(stream_id)
    status = stream_service.get_source_status(stream)
    return RealStreamResponse.from_domain(stream, status)


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
