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
    VideoSearchResult,
    VideoSearchResultPage,
)

_KST = ZoneInfo("Asia/Seoul")
_TOKEN_PATTERN = re.compile(r"[0-9a-z가-힣]+")
_HOUR_PATTERN = re.compile(r"(?P<hour>\d{1,2})\s*시\s*(?P<direction>이후|전|이전)")
_GENERIC_TERMS = frozenset(
    {
        "영상",
        "데모",
        "검색",
        "찾아줘",
        "보여줘",
        "있던",
        "있는",
        "강의실",
        "에서",
        "어제",
        "오늘",
        "이후",
        "이전",
    }
)
_SEMANTIC_ALIASES: dict[str, tuple[str, ...]] = {
    "인원 잔류": ("사람", "인원", "남아", "잔류"),
    "마감 후": ("마감", "업무 외", "방과 후"),
    "좌석 점유": ("좌석", "점유", "사용 중"),
    "입실": ("입실", "들어", "입장"),
    "이동": ("이동", "움직임", "움직이는"),
    "장비 구역": ("장비", "실습"),
    "비어 있음": ("비어", "빈 공간", "아무도 없"),
    "움직임 없음": ("움직임 없", "정지"),
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
            raise VideoSearchInputError("검색 문장은 1자 이상 200자 이하여야 합니다.")
        from_at = self._aware_utc(from_at)
        to_at = self._aware_utc(to_at)
        if from_at is not None and to_at is not None and from_at > to_at:
            raise VideoSearchInputError("검색 시작 시각은 종료 시각보다 늦을 수 없습니다.")
        if limit < 1 or limit > 50:
            raise VideoSearchInputError("검색 결과 수는 1~50이어야 합니다.")

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
            reason = " · ".join(matched) if matched else "전체 데모 카탈로그"
            matches.append(
                VideoSearchResult(
                    clip=clip,
                    matched_terms=matched,
                    match_reason=f"고정 메타데이터 일치: {reason}",
                )
            )
        matches.sort(key=lambda item: (item.clip.started_at, item.clip.id), reverse=True)
        return VideoSearchResultPage(
            items=matches[:limit], total=len(matches), limit=limit
        )

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
        if "어제" in query:
            return reference
        if "오늘" in query:
            return reference + timedelta(days=1)
        return None

    @staticmethod
    def _hour_filter(query: str) -> tuple[int, bool] | None:
        match = _HOUR_PATTERN.search(query)
        if match is None:
            return None
        hour = int(match.group("hour"))
        if hour < 0 or hour > 23:
            raise VideoSearchInputError("검색 문장의 시각은 0~23시여야 합니다.")
        if 1 <= hour <= 7:
            hour += 12
        return hour, match.group("direction") == "이후"

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
                or bool(re.fullmatch(r"\d{1,2}시", token))
                or any(alias in token or token in alias for alias in covered_aliases)
                or token.endswith(("에는", "에서", "으로", "이후", "이전"))
            ):
                continue
            terms.append(token)
        return tuple(dict.fromkeys(terms))

    @staticmethod
    def _strip_particle(token: str) -> str:
        for suffix in (
            "에서는",
            "으로",
            "에서",
            "에게",
            "부터",
            "까지",
            "에는",
            "에",
            "의",
            "을",
            "를",
            "이",
            "가",
            "은",
            "는",
        ):
            if token.endswith(suffix) and len(token) > len(suffix) + 1:
                return token[: -len(suffix)]
        return token

    @staticmethod
    def _aware_utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise VideoSearchInputError("검색 시각은 timezone을 포함해야 합니다.")
        return value.astimezone(UTC)

    @staticmethod
    def _normalize(value: str) -> str:
        return " ".join(unicodedata.normalize("NFKC", value).casefold().split())
