"""Immutable values for the synthetic monitoring demo catalog."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class DemoStreamStatus(StrEnum):
    CONNECTED = "CONNECTED"
    NO_VIDEO = "NO_VIDEO"


@dataclass(frozen=True)
class DemoStream:
    id: str
    classroom_id: str
    classroom_code: str
    classroom_name: str
    camera_label: str
    status: DemoStreamStatus
    synthetic_variant: str | None
    poster_path: str
    last_updated_at: datetime


@dataclass(frozen=True)
class DemoVideoClip:
    id: str
    title: str
    classroom_id: str
    classroom_code: str
    classroom_name: str
    started_at: datetime
    ended_at: datetime
    tags: tuple[str, ...]
    summary: str
    synthetic_variant: str
    poster_path: str

    @property
    def duration_seconds(self) -> int:
        return int((self.ended_at - self.started_at).total_seconds())


@dataclass(frozen=True)
class VideoSearchResult:
    clip: DemoVideoClip
    matched_terms: tuple[str, ...]
    match_reason: str


@dataclass(frozen=True)
class VideoSearchResultPage:
    items: list[VideoSearchResult]
    total: int
    limit: int
