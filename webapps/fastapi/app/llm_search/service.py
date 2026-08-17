"""검증된 계획을 실제 탐지 조회로 실행한다.

**FastAPI에 의존하지 않는다.** Request도 HTTPException도 쓰지 않는다.

이 서비스는 세 기능을 가로지른다 — 탐지 이벤트(`student_monitoring`), 카메라 등록
정보(`video_monitoring`), 스냅샷(`snapshots`). 결정 0002가 "두 개 이상의 기능을
가로지르고 화면과 API가 그 조합을 함께 쓰면 그 기능의 service.py가 파사드가 된다"고
정한 자리다.

## 왜 저장소는 포트로, 스냅샷은 서비스로 받는가

탐지·스트림은 포트를 받는다. 두 기능 모두 조회용 서비스 메서드가 없어서 기존
라우터도 저장소를 직접 부른다. 스냅샷만 `SnapshotService`를 받는데, 객체 키 규칙의
해석과 저장소 장애 처리가 그 안에 있기 때문이다. 포트를 직접 받으면 키 규칙이
worker·snapshots에 이어 **세 번째로 복사된다.**

## 결과를 왜 줄이는가

탐지 이벤트는 카메라당 프레임마다 한 건이다. 한 시간 범위면 거의 같은 내용이 수천
건 쌓이고, 앞에서 50건을 잘라 보여주면 사용자는 처음 몇 초만 보게 된다. 그래서
**탐지 인원이 직전과 달라진 순간만** 남긴다. worker가 스냅샷을 올리는 기준과 같은
판단이라(탐지 개수 변화), 남은 줄이 실제로 이미지가 있는 줄과도 겹친다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
from datetime import datetime

from ..snapshots.errors import SnapshotStorageUnavailableError
from ..snapshots.models import build_snapshot_key
from ..snapshots.service import SnapshotService
from ..student_monitoring.models import DetectionEvent
from ..student_monitoring.ports import DetectionEventRepository
from ..video_monitoring.models import VideoStream
from ..video_monitoring.ports import VideoStreamRepository
from .errors import LlmSearchPlanInvalidError
from .models import CameraChoice, DetectionHit, IdentifiedStudent, SearchOutcome, SearchQuery
from .planning import MAX_LIMIT, parse_plan
from .ports import PlanPrompt, QueryPlanner
from .prompts import build_system_prompt

logger = logging.getLogger(__name__)

__all__ = ["LlmSearchService"]


class LlmSearchService:
    def __init__(
        self,
        planner: QueryPlanner,
        detection_repository: DetectionEventRepository,
        stream_repository: VideoStreamRepository,
        snapshot_service: SnapshotService,
        *,
        max_span_days: int,
        scan_limit: int,
        clock: Callable[[], datetime],
    ) -> None:
        self._planner = planner
        self._detections = detection_repository
        self._streams = stream_repository
        self._snapshots = snapshot_service
        self._max_span_days = max_span_days
        self._scan_limit = scan_limit
        self._clock = clock

    def search(self, question: str, *, limit: int) -> SearchOutcome:
        """자연어 질문 하나를 탐지 검색 결과로 바꾼다."""
        now = self._clock()
        streams = self._streams.find_all_enabled()

        # 호출자가 요청한 상한이 곧 모델에게 알리는 상한이다. 지시문과 검증이 다른
        # 수를 쓰면 모델은 규격을 지켰는데 결과만 조용히 줄어든다.
        ceiling = min(limit, MAX_LIMIT)
        choices = [_to_choice(stream) for stream in streams]
        query = self._plan(question, now=now, cameras=choices, ceiling=ceiling)

        targets, notes = self._resolve_targets(query, streams)
        events, scan_truncated = self._collect_events(query, targets)

        changes = [event for target in targets for event in _keep_changes(events.get(target, []))]
        changes.sort(key=lambda event: (event.captured_at, event.sequence), reverse=True)

        limited = changes[: query.limit]
        truncated = scan_truncated or len(changes) > len(limited)

        classroom_by_camera = {stream.camera_id: stream.classroom_id for stream in targets}
        hits, snapshot_lookup_failed = self._to_hits(limited, classroom_by_camera)

        return SearchOutcome(
            query=replace(query, notes=query.notes + tuple(notes)),
            hits=tuple(hits),
            truncated=truncated,
            snapshot_lookup_failed=snapshot_lookup_failed,
        )

    def _plan(
        self,
        question: str,
        *,
        now: datetime,
        cameras: Sequence[CameraChoice],
        ceiling: int,
    ) -> SearchQuery:
        """질문을 검증된 검색 조건으로 바꾼다. **한 번은 다시 시도한다.**

        작은 모델은 첫 응답에서 형식이 깨져도 다시 물으면 붙는 일이 흔하다. 한 번의
        실패로 422를 돌려주면 사용자는 아무 잘못이 없는데 질문을 고치라는 말을 듣는다.

        **닿지 못한 경우는 재시도하지 않는다.** `LlmSearchPlannerUnavailableError`는
        여기서 잡지 않으므로 그대로 올라간다. 서버가 죽었거나 타임아웃이 난 상황이라
        한 번 더 부르면 사용자를 두 배로 기다리게 할 뿐이다. 반대로 규격 위반은
        **모델이 이미 답을 냈다는 뜻**이라 재시도 비용이 첫 시도와 같다.
        """
        try:
            return self._attempt(question, now=now, cameras=cameras, ceiling=ceiling, retry=False)
        except LlmSearchPlanInvalidError as error:
            # 사유는 우리가 정의한 코드다. 모델이 쓴 글자가 아니다.
            logger.info("계획이 규격을 벗어나 한 번 더 시도한다: %s", error.details)
        return self._attempt(question, now=now, cameras=cameras, ceiling=ceiling, retry=True)

    def _attempt(
        self,
        question: str,
        *,
        now: datetime,
        cameras: Sequence[CameraChoice],
        ceiling: int,
        retry: bool,
    ) -> SearchQuery:
        raw = self._planner.plan(
            PlanPrompt(
                system=build_system_prompt(
                    now=now, cameras=cameras, max_limit=ceiling, retry=retry
                ),
                question=question,
                now=now,
            )
        )
        # 원문은 로그에만 남긴다. 응답에 실으면 프롬프트에 넣은 카메라 목록이
        # 되돌아 나올 수 있다.
        logger.debug("검색 계획 원문: %s", raw)
        return parse_plan(raw, max_span_days=self._max_span_days, limit_ceiling=ceiling)

    def _resolve_targets(
        self, query: SearchQuery, enabled: Sequence[VideoStream]
    ) -> tuple[list[VideoStream], list[str]]:
        """계획이 가리키는 카메라를 실제 등록 정보로 좁힌다.

        찾지 못해도 오류로 만들지 않는다. "그런 강의실은 없다"는 정상적인 0건이고,
        다만 **왜 0건인지**를 사용자가 알아야 하므로 사유를 남긴다.
        """
        if query.camera_id is not None:
            # 카메라를 콕 집었을 때는 비활성 카메라도 찾는다. 지금 꺼져 있어도
            # 과거 이력은 남아 있고, 사용자가 물은 것은 과거다.
            stream = self._streams.find_by_camera_id(query.camera_id)
            if stream is None:
                return [], [f"등록되지 않은 카메라({query.camera_id})라 결과를 찾지 못했습니다."]
            return [stream], []

        if query.classroom_id is not None:
            matched = [stream for stream in enabled if stream.classroom_id == query.classroom_id]
            if not matched:
                return [], [
                    f"{query.classroom_id} 강의실에 사용 중인 카메라가 없어 결과를 찾지 못했습니다."
                ]
            return matched, []

        if not enabled:
            return [], ["사용 중인 카메라가 없어 결과를 찾지 못했습니다."]
        return list(enabled), []

    def _collect_events(
        self, query: SearchQuery, targets: Sequence[VideoStream]
    ) -> tuple[dict[VideoStream, list[DetectionEvent]], bool]:
        """카메라별로 기간 안의 탐지 이벤트를 모은다.

        저장소는 최신순으로 잘라 주므로, 상한에 걸리면 **범위 앞쪽이 통째로 빠진다.**
        그 사실을 호출자에게 알려야 화면이 "이게 전부"라고 말하지 않는다.
        """
        collected: dict[VideoStream, list[DetectionEvent]] = {}
        truncated = False
        for stream in targets:
            page = self._detections.find_by_camera_and_period(
                stream.camera_id,
                query.from_at,
                query.to_at,
                limit=self._scan_limit,
                cursor=None,
            )
            collected[stream] = list(page.items)
            if page.total > len(page.items):
                truncated = True
        return collected, truncated

    def _to_hits(
        self, events: Sequence[DetectionEvent], classroom_by_camera: dict[str, str]
    ) -> tuple[list[DetectionHit], bool]:
        """결과 줄을 만들고 스냅샷 키가 실재하는지 확인한다.

        저장소 장애는 검색 전체를 실패시키지 않는다. 이미지를 못 붙였을 뿐 메타데이터는
        유효하고, 둘을 함께 잃으면 운영자가 판단할 근거가 사라진다.
        """
        available, lookup_failed = self._available_keys(events)

        hits: list[DetectionHit] = []
        for event in events:
            identified = tuple(
                IdentifiedStudent(
                    student_id=detection.student_id,
                    identity_confidence=detection.identity_confidence,
                )
                for detection in event.detections
                if detection.student_id
            )
            key = build_snapshot_key(event.camera_id, event.captured_at)
            hits.append(
                DetectionHit(
                    event_id=event.event_id,
                    camera_id=event.camera_id,
                    resolved_classroom_id=classroom_by_camera.get(event.camera_id, ""),
                    captured_at=event.captured_at,
                    detection_count=len(event.detections),
                    identified=identified,
                    unidentified_count=len(event.detections) - len(identified),
                    snapshot_key=key if key in available else None,
                )
            )
        return hits, lookup_failed

    def _available_keys(self, events: Iterable[DetectionEvent]) -> tuple[frozenset[str], bool]:
        """결과에 등장하는 (카메라, 날짜) 조합만 저장소에 물어본다.

        조회 기간 전체가 아니라 **남은 결과가 걸친 날짜만** 훑는다. 인원 변화 시점만
        남긴 뒤라 조합 수가 결과 줄 수를 넘지 않는다.
        """
        days = {(event.camera_id, event.captured_at.date()) for event in events}
        keys: set[str] = set()
        for camera_id, day in sorted(days):
            try:
                keys.update(self._snapshots.existing_keys(camera_id, day))
            except SnapshotStorageUnavailableError:
                logger.warning("스냅샷 저장소를 확인하지 못해 이미지 없이 응답한다")
                return frozenset(), True
        return frozenset(keys), False


def _to_choice(stream: VideoStream) -> CameraChoice:
    return CameraChoice(
        camera_id=stream.camera_id,
        classroom_id=stream.classroom_id,
        label=stream.camera_label,
    )


def _keep_changes(events: Sequence[DetectionEvent]) -> list[DetectionEvent]:
    """탐지 인원이 직전과 달라진 이벤트만 남긴다.

    한 카메라 안에서만 비교한다. 카메라를 섞어 세면 A교실에 사람이 들어온 것이
    B교실에서 나간 것으로 상쇄된다.

    범위의 첫 이벤트는 언제나 남긴다. 그것이 이 구간의 시작 상태이고, 빼면 "처음부터
    두 명이 있었다"가 화면에서 사라진다.
    """
    ordered = sorted(events, key=lambda event: (event.captured_at, event.sequence))
    kept: list[DetectionEvent] = []
    previous: int | None = None
    for event in ordered:
        count = len(event.detections)
        if previous is None or count != previous:
            kept.append(event)
            previous = count
    return kept
