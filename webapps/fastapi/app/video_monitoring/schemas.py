"""HTTP schemas for the synthetic monitoring demo."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import (
    DemoStream,
    PlaybackSession,
    SourceStatus,
    VideoSearchResult,
    VideoSearchResultPage,
    VideoStream,
)


class DemoStreamResponse(BaseModel):
    id: str
    classroom_id: str
    classroom_code: str
    classroom_name: str
    camera_label: str
    status: str
    playback_kind: Literal["SYNTHETIC_CANVAS", "UNAVAILABLE"]
    synthetic_variant: str | None
    poster_path: str
    last_updated_at: datetime
    is_demo: Literal[True] = True

    @classmethod
    def from_domain(cls, item: DemoStream) -> DemoStreamResponse:
        return cls(
            id=item.id,
            classroom_id=item.classroom_id,
            classroom_code=item.classroom_code,
            classroom_name=item.classroom_name,
            camera_label=item.camera_label,
            status=item.status.value,
            playback_kind=(
                "SYNTHETIC_CANVAS" if item.synthetic_variant is not None else "UNAVAILABLE"
            ),
            synthetic_variant=item.synthetic_variant,
            poster_path=item.poster_path,
            last_updated_at=item.last_updated_at,
        )


class RealStreamResponse(BaseModel):
    id: str
    camera_id: str
    classroom_id: str
    camera_label: str
    status: str
    playback_kind: str
    playback_path: str | None
    last_frame_at: datetime | None
    last_detection_at: datetime | None
    is_demo: Literal[False] = False

    @classmethod
    def from_domain(cls, item: VideoStream, status: SourceStatus) -> RealStreamResponse:
        return cls(
            id=item.id,
            camera_id=item.camera_id,
            classroom_id=item.classroom_id,
            camera_label=item.camera_label,
            status=status.value,
            playback_kind=item.playback_kind.value,
            playback_path=item.playback_path,
            last_frame_at=item.last_frame_at,
            last_detection_at=item.last_detection_at,
        )


class StreamListResponse(BaseModel):
    items: list[DemoStreamResponse | RealStreamResponse]
    total: int


class DemoStreamListResponse(BaseModel):
    items: list[DemoStreamResponse]
    total: int


class VideoSearchRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    query: str = Field(min_length=1, max_length=200)
    classroom_id: str | None = Field(default=None, max_length=128)
    from_at: datetime | None = Field(default=None, alias="from")
    to_at: datetime | None = Field(default=None, alias="to")
    limit: int = Field(default=20, ge=1, le=50)

    @model_validator(mode="after")
    def validate_time_range(self) -> VideoSearchRequest:
        for value in (self.from_at, self.to_at):
            if value is not None and value.tzinfo is None:
                raise ValueError("video search datetimes must include a timezone")
        if self.from_at is not None and self.to_at is not None and self.from_at > self.to_at:
            raise ValueError("from must not be later than to")
        return self


class VideoSearchResultResponse(BaseModel):
    id: str
    title: str
    classroom_id: str
    classroom_code: str
    classroom_name: str
    started_at: datetime
    ended_at: datetime
    duration_seconds: int
    tags: list[str]
    summary: str
    synthetic_variant: str
    poster_path: str
    matched_terms: list[str]
    match_reason: str
    is_demo: Literal[True] = True

    @classmethod
    def from_domain(cls, item: VideoSearchResult) -> VideoSearchResultResponse:
        clip = item.clip
        return cls(
            id=clip.id,
            title=clip.title,
            classroom_id=clip.classroom_id,
            classroom_code=clip.classroom_code,
            classroom_name=clip.classroom_name,
            started_at=clip.started_at,
            ended_at=clip.ended_at,
            duration_seconds=clip.duration_seconds,
            tags=list(clip.tags),
            summary=clip.summary,
            synthetic_variant=clip.synthetic_variant,
            poster_path=clip.poster_path,
            matched_terms=list(item.matched_terms),
            match_reason=item.match_reason,
        )


class VideoSearchResponse(BaseModel):
    items: list[VideoSearchResultResponse]
    total: int
    limit: int

    @classmethod
    def from_domain(cls, page: VideoSearchResultPage) -> VideoSearchResponse:
        return cls(
            items=[VideoSearchResultResponse.from_domain(item) for item in page.items],
            total=page.total,
            limit=page.limit,
        )


class PlaybackSessionCreateResponse(BaseModel):
    """재생 세션 생성 응답 (결정 0014).

    opaque session_id, FastAPI signaling URL, expires_at만 포함한다.
    MediaMTX 주소·포트·RTSP URL·자격 증명은 넣지 않는다.
    """

    session_id: str
    signaling_url: str
    expires_at: datetime

    @classmethod
    def from_domain(
        cls, session: PlaybackSession, signaling_url: str
    ) -> PlaybackSessionCreateResponse:
        return cls(
            session_id=session.session_id,
            signaling_url=signaling_url,
            expires_at=session.expires_at,
        )
