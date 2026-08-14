"""Deterministic catalog filtering; no AI, persistence, or external calls."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from .catalog import DEMO_STREAMS, DEMO_VIDEO_CLIPS
from .errors import DemoStreamNotFoundError, VideoSearchInputError
from .models import (
    DemoStream,
    DemoStreamStatus,
    DemoVideoClip,
    SourceStatus,
    VideoSearchResult,
    VideoSearchResultPage,
    VideoStream,
)
from .ports import VideoStreamRepository

_KST = ZoneInfo("Asia/Seoul")
_TOKEN_PATTERN = re.compile(r"[0-9a-z\uac00-\ud7a3]+")
_HOUR_PATTERN = re.compile(
    r"(?P<hour>\d{1,2})\s*\uc2dc\s*(?P<direction>\uc774\ud6c4|\uc804|\uc774\uc804)"
)
_GENERIC_TERMS = frozenset(
    {
        "\uc601\uc0c1",
        "\ub370\ubaa8",
        "\uac80\uc0c9",
        "\ucc3e\uc544\uc8fc",
        "\ubcf4\uc5ec\uc8fc",
        "\uc788\ub358",
        "\uc788\ub294",
        "\uac15\uc758\uc2e4",
        "\uc5d0\uc11c",
        "\uc5b4\uc81c",
        "\uc624\ub298",
        "\uc774\ud6c4",
        "\uc774\uc804",
    }
)
_SEMANTIC_ALIASES: dict[str, tuple[str, ...]] = {
    "\uc778\uc6d0 \uc794\ub954": ("\uc0ac\ub78c", "\uc778\uc6d0", "\ub0a8\uc544", "\uc794\ub954"),
    "\ub9c8\uac10 \ud6c4": ("\ub9c8\uac10", "\uc5c5\ubb34 \uc678", "\ubc29\uacfc \ud6c4"),
    "\uc88c\uc11d \uc810\uc720": ("\uc88c\uc11d", "\uc810\uc720", "\uc0ac\uc6a9 \uc911"),
    "\uc785\uc2e4": ("\uc785\uc2e4", "\ub4e4\uc5b4", "\uc785\uc7a5"),
    "\uc774\ub3d9": ("\uc774\ub3d9", "\uc6b4\ub3d9\uc785", "\uc6b4\ub3d9\ud558\ub294"),
    "\uc7a5\ube44 \uad6c\uc5ed": ("\uc7a5\ube44", "\uc2e4\uc2b5"),
    "\ube44\uc5b4 \uc788\uc74c": (
        "\ube44\uc5b4",
        "\ube48 \uacf5\uac04",
        "\uc544\ubb34\ub3c4 \uc5c6",
    ),
    "\uc6b4\ub3d9\uc785 \uc5c6\uc74c": ("\uc6b4\ub3d9\uc785 \uc5c6", "\uc815\uc9c0"),
}


class VideoDemoService:
    def __init__(
        self,
        *,
        streams: tuple[DemoStream, ...] = DEMO_STREAMS,
        clips: tuple[DemoVideoClip, ...] = DEMO_VIDEO_CLIPS,
        clock: Callable[[], datetime],
    ) -> None:
        self._streams = streams
        self._clips = clips
        self._clock = clock

    def current_time(self) -> datetime:
        return self._clock().astimezone(UTC)

    def list_streams(
        self,
        *,
        search: str | None,
        classroom_id: str | None,
        status: DemoStreamStatus | None,
    ) -> list[DemoStream]:
        items = list(self._streams)
        if classroom_id:
            items = [item for item in items if item.classroom_id == classroom_id]
        if status is not None:
            items = [item for item in items if item.status == status]
        if search and (normalized := self._normalize(search)):
            items = [
                item
                for item in items
                if normalized
                in self._normalize(
                    f"{item.classroom_code} {item.classroom_name} {item.camera_label}"
                )
            ]
        return items

    def get_stream(self, stream_id: str) -> DemoStream:
        stream = next((item for item in self._streams if item.id == stream_id), None)
        if stream is None:
            raise DemoStreamNotFoundError()
        return stream

    def search_videos(
        self,
        query: str,
        *,
        classroom_id: str | None,
        from_at: datetime | None,
        to_at: datetime | None,
        limit: int,
    ) -> VideoSearchResultPage:
        normalized_query = self._normalize(query)
        if not normalized_query or len(query.strip()) > 200:
            raise VideoSearchInputError("Search query must be 1-200 characters.")
        from_at = self._aware_utc(from_at)
        to_at = self._aware_utc(to_at)
        if from_at is not None and to_at is not None and from_at > to_at:
            raise VideoSearchInputError("Start time must not be later than end time.")
        if limit < 1 or limit > 50:
            raise VideoSearchInputError("Result count must be 1-50.")

        concepts = self._semantic_concepts(normalized_query)
        raw_terms = self._raw_terms(normalized_query, concepts)
        query_date = self._query_date(normalized_query)
        hour_filter = self._hour_filter(normalized_query)
        matches: list[VideoSearchResult] = []
        for clip in self._clips:
            if classroom_id and clip.classroom_id != classroom_id:
                continue
            if from_at is not None and clip.ended_at < from_at:
                continue
            if to_at is not None and clip.started_at > to_at:
                continue
            local_start = clip.started_at.astimezone(_KST)
            if query_date is not None and local_start.date() != query_date:
                continue
            if hour_filter is not None:
                hour, after = hour_filter
                if (after and local_start.hour < hour) or (not after and local_start.hour >= hour):
                    continue
            haystack = self._normalize(
                " ".join(
                    (
                        clip.title,
                        clip.classroom_code,
                        clip.classroom_name,
                        clip.summary,
                        *clip.tags,
                    )
                )
            )
            if any(concept not in haystack for concept in concepts):
                continue
            if any(term not in haystack for term in raw_terms):
                continue
            matched = tuple(dict.fromkeys((*concepts, *raw_terms)))
            reason = (
                " \u00b7 ".join(matched)
                if matched
                else "\uc804\uccb4 \ub370\ubaa8 \uce90\ud0c8\ub85c\uadf8"
            )
            matches.append(
                VideoSearchResult(
                    clip=clip,
                    matched_terms=matched,
                    match_reason=f"\uace0\uc815 \uba54\ud0c0\ub370\uc774\ud130 \uc77c\uce58: {reason}",
                )
            )
        matches.sort(key=lambda item: (item.clip.started_at, item.clip.id), reverse=True)
        return VideoSearchResultPage(items=matches[:limit], total=len(matches), limit=limit)

    def classroom_options(self) -> list[tuple[str, str]]:
        return list(
            dict.fromkeys(
                (item.classroom_id, f"{item.classroom_code} {item.classroom_name}")
                for item in self._streams
            )
        )

    def _query_date(self, query: str) -> date | None:
        if not self._clips:
            return None
        reference = max(item.started_at.astimezone(_KST).date() for item in self._clips)
        if "\uc5b4\uc81c" in query:
            return reference
        if "\uc624\ub298" in query:
            return reference + timedelta(days=1)
        return None

    @staticmethod
    def _hour_filter(query: str) -> tuple[int, bool] | None:
        match = _HOUR_PATTERN.search(query)
        if match is None:
            return None
        hour = int(match.group("hour"))
        if hour < 0 or hour > 23:
            raise VideoSearchInputError("Hour must be 0-23.")
        if 1 <= hour <= 7:
            hour += 12
        return hour, match.group("direction") == "\uc774\ud6c4"

    @staticmethod
    def _semantic_concepts(query: str) -> tuple[str, ...]:
        return tuple(
            concept
            for concept, aliases in _SEMANTIC_ALIASES.items()
            if any(alias in query for alias in aliases)
        )

    @staticmethod
    def _raw_terms(query: str, concepts: tuple[str, ...]) -> tuple[str, ...]:
        covered_aliases = {
            alias
            for concept in concepts
            for alias in _SEMANTIC_ALIASES[concept]
            if " " not in alias
        }
        terms = []
        for token in _TOKEN_PATTERN.findall(query):
            token = VideoDemoService._strip_particle(token)
            if (
                len(token) < 2
                or token in _GENERIC_TERMS
                or token.isdigit()
                or bool(re.fullmatch(r"\d{1,2}\uc2dc", token))
                or any(alias in token or token in alias for alias in covered_aliases)
                or token.endswith(
                    ("\uc5d0\ub294", "\uc5d0\uc11c", "\uc73c\ub85c", "\uc774\ud6c4", "\uc774\uc804")
                )
            ):
                continue
            terms.append(token)
        return tuple(dict.fromkeys(terms))

    @staticmethod
    def _strip_particle(token: str) -> str:
        for suffix in (
            "\uc5d0\uc11c\ub294",
            "\uc73c\ub85c",
            "\uc5d0\uc11c",
            "\uc5d0\uac8c",
            "\ubd80\ud130",
            "\uae4c\uc9c0",
            "\uc5d0\ub294",
            "\uc5d0",
            "\uc758",
            "\uc744",
            "\ub97c",
            "\uc774",
            "\uac00",
            "\uc740",
            "\ub294",
        ):
            if token.endswith(suffix) and len(token) > len(suffix) + 1:
                return token[: -len(suffix)]
        return token

    @staticmethod
    def _aware_utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise VideoSearchInputError("Search time must include timezone.")
        return value.astimezone(UTC)

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


class VideoStreamService:
    """Real video stream service."""

    def __init__(
        self,
        repository: VideoStreamRepository,
        stale_seconds: int,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._stale_seconds = stale_seconds
        self._clock = clock

    def list_streams(self) -> list[VideoStream]:
        """List all enabled streams."""
        return self._repository.find_all_enabled()

    def get_stream(self, camera_id: str) -> VideoStream:
        """Get stream by camera ID."""
        stream = self._repository.find_by_camera_id(camera_id)
        if stream is None:
            raise DemoStreamNotFoundError()
        return stream

    def get_source_status(self, stream: VideoStream) -> SourceStatus:
        """Calculate source status based on last detection time."""
        if stream.last_detection_at is None:
            return SourceStatus.UNKNOWN

        now = self._clock().astimezone(UTC)
        elapsed = (now - stream.last_detection_at).total_seconds()

        if elapsed < self._stale_seconds:
            return SourceStatus.CONNECTED
        elif elapsed < self._stale_seconds * 2:
            return SourceStatus.STALE
        else:
            return SourceStatus.NO_VIDEO
