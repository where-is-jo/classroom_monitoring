"""계획을 실제 탐지 조회로 실행하는 계약.

LLM도 HTTP도 없이 돈다. 대역은 고정된 계획 문자열 하나를 돌려주고, 저장소는 기존
메모리 어댑터를 그대로 쓴다.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta

import pytest

from app.llm_search.errors import LlmSearchPlanInvalidError, LlmSearchPlannerUnavailableError
from app.llm_search.ports import PlanPrompt, QueryPlanner
from app.llm_search.service import LlmSearchService
from app.snapshots.errors import SnapshotStorageUnavailableError
from app.snapshots.models import build_snapshot_key
from app.snapshots.ports import ObjectContent, StoredObject
from app.snapshots.service import SnapshotService
from app.student_monitoring.adapters.memory_repository import MemoryDetectionEventRepository
from app.student_monitoring.models import Detection, DetectionEvent, FrameInfo
from app.video_monitoring.adapters.memory_repository import MemoryVideoStreamRepository
from app.video_monitoring.models import PlaybackKind, VideoStream

_NOW = datetime(2026, 8, 14, 10, 0, tzinfo=UTC)
_FROM = "2026-08-14T00:00:00Z"
_TO = "2026-08-15T00:00:00Z"


class FakePlanner:
    """고정된 계획을 돌려준다. 마지막으로 받은 프롬프트를 기억한다."""

    def __init__(self, plan: Mapping[str, object] | None = None, *, fails: bool = False) -> None:
        self._plan = plan
        self._fails = fails
        self.last_prompt: PlanPrompt | None = None
        self.calls = 0

    def plan(self, prompt: PlanPrompt) -> str:
        self.last_prompt = prompt
        self.calls += 1
        if self._fails:
            raise LlmSearchPlannerUnavailableError()
        if self._plan is None:
            return "무슨 말인지 모르겠습니다."
        return json.dumps(self._plan)


class SequencePlanner:
    """호출마다 다른 원문을 돌려준다. 재시도를 보려면 두 번의 프롬프트가 필요하다."""

    def __init__(self, *raws: str) -> None:
        self._raws = list(raws)
        self.prompts: list[PlanPrompt] = []

    def plan(self, prompt: PlanPrompt) -> str:
        self.prompts.append(prompt)
        return self._raws.pop(0)


class FakeStorage:
    def __init__(self, keys: list[str] | None = None, *, fails: bool = False) -> None:
        self._keys = keys or []
        self._fails = fails

    def list_objects(self, prefix: str = "") -> Iterator[StoredObject]:
        if self._fails:
            raise SnapshotStorageUnavailableError()
        for key in sorted(self._keys):
            if key.startswith(prefix):
                yield StoredObject(key=key, size_bytes=10, last_modified=_NOW)

    def get_object(self, key: str) -> ObjectContent | None:
        raise AssertionError("검색은 이미지 바이트를 읽지 않는다")


def _stream(camera_id: str, classroom_id: str, *, enabled: bool = True) -> VideoStream:
    return VideoStream(
        id=f"stream-{camera_id}",
        camera_id=camera_id,
        classroom_id=classroom_id,
        camera_label=f"{classroom_id} 카메라",
        playback_kind=PlaybackKind.WEBRTC,
        playback_path=None,
        enabled=enabled,
        last_frame_at=None,
        last_detection_at=None,
        is_demo=False,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _event(
    camera_id: str, minute: int, people: int, *, student_ids: tuple[str, ...] = ()
) -> DetectionEvent:
    detections = tuple(
        Detection(
            detection_id=f"{camera_id}-{minute}-{index}",
            class_id=0,
            class_name="person",
            confidence=0.9,
            bbox=(0, 0, 10, 10),
            student_id=student_ids[index] if index < len(student_ids) else None,
            identity_confidence=0.8 if index < len(student_ids) else None,
            face_bbox=None,
        )
        for index in range(people)
    )
    captured_at = datetime(2026, 8, 14, 1, 0, tzinfo=UTC) + timedelta(minutes=minute)
    return DetectionEvent(
        event_id=f"{camera_id}-{minute}",
        camera_id=camera_id,
        stream_id="",
        classroom_id="",
        captured_at=captured_at,
        sequence=minute,
        frame=FrameInfo(width_pixels=640, height_pixels=480),
        detections=detections,
        received_at=captured_at,
        schema_version=1,
    )


def _build(
    *,
    planner: QueryPlanner | None = None,
    events: list[DetectionEvent] | None = None,
    streams: list[VideoStream] | None = None,
    snapshot_keys: list[str] | None = None,
    snapshot_fails: bool = False,
    scan_limit: int = 500,
) -> LlmSearchService:
    detections = MemoryDetectionEventRepository()
    for event in events or []:
        detections.save(event)

    stream_repository = MemoryVideoStreamRepository()
    for stream in streams if streams is not None else [_stream("camera-01", "A101")]:
        stream_repository.save(stream)

    default_plan = {
        "intent": "detection_search",
        "camera_id": None,
        "classroom_id": None,
        "from": _FROM,
        "to": _TO,
        "limit": 20,
    }
    return LlmSearchService(
        planner or FakePlanner(default_plan),
        detections,
        stream_repository,
        SnapshotService(FakeStorage(snapshot_keys, fails=snapshot_fails)),
        max_span_days=7,
        scan_limit=scan_limit,
        clock=lambda: _NOW,
    )


def test_탐지_인원이_바뀐_시점만_남긴다() -> None:
    """프레임마다 한 건이라 전부 보여주면 거의 같은 줄이 수천 개가 된다."""
    events = [
        _event("camera-01", 0, 1),
        _event("camera-01", 1, 1),
        _event("camera-01", 2, 2),
        _event("camera-01", 3, 2),
        _event("camera-01", 4, 0),
    ]

    outcome = _build(events=events).search("오늘 누가 왔어?", limit=20)

    assert [hit.event_id for hit in outcome.hits] == [
        "camera-01-4",
        "camera-01-2",
        "camera-01-0",
    ]
    assert outcome.truncated is False


def test_범위의_첫_이벤트는_변화가_없어도_남긴다() -> None:
    """빼면 '처음부터 두 명이 있었다'가 화면에서 사라진다."""
    events = [_event("camera-01", 0, 2), _event("camera-01", 1, 2)]

    outcome = _build(events=events).search("오늘", limit=20)

    assert [hit.event_id for hit in outcome.hits] == ["camera-01-0"]
    assert outcome.hits[0].detection_count == 2


def test_카메라별로_따로_비교한다() -> None:
    """섞어 세면 A교실에 들어온 것이 B교실에서 나간 것으로 상쇄된다."""
    streams = [_stream("camera-01", "A101"), _stream("camera-02", "B203")]
    events = [
        _event("camera-01", 0, 1),
        _event("camera-02", 1, 1),
        _event("camera-01", 2, 1),
    ]

    outcome = _build(events=events, streams=streams).search("오늘", limit=20)

    assert {hit.event_id for hit in outcome.hits} == {"camera-01-0", "camera-02-1"}


def test_강의실을_지정하면_그_강의실_카메라만_본다() -> None:
    streams = [_stream("camera-01", "A101"), _stream("camera-02", "B203")]
    plan = {
        "intent": "detection_search",
        "classroom_id": "B203",
        "from": _FROM,
        "to": _TO,
    }
    events = [_event("camera-01", 0, 1), _event("camera-02", 0, 3)]

    service = _build(planner=FakePlanner(plan), events=events, streams=streams)
    outcome = service.search("B203", limit=20)

    assert [hit.camera_id for hit in outcome.hits] == ["camera-02"]
    assert outcome.hits[0].resolved_classroom_id == "B203"


def test_없는_강의실은_오류가_아니라_사유가_붙은_빈_결과다() -> None:
    """'그런 강의실 없음'은 정상 0건이다. 다만 왜 0건인지는 알려준다."""
    plan = {
        "intent": "detection_search",
        "classroom_id": "없는교실",
        "from": _FROM,
        "to": _TO,
    }

    service = _build(planner=FakePlanner(plan), events=[_event("camera-01", 0, 1)])
    outcome = service.search("?", limit=20)

    assert outcome.hits == ()
    assert any("없는교실" in note for note in outcome.query.notes)


def test_없는_카메라도_사유가_붙은_빈_결과다() -> None:
    plan = {
        "intent": "detection_search",
        "camera_id": "camera-99",
        "from": _FROM,
        "to": _TO,
    }

    outcome = _build(planner=FakePlanner(plan)).search("?", limit=20)

    assert outcome.hits == ()
    assert any("camera-99" in note for note in outcome.query.notes)


def test_저장소_상한에_걸리면_잘렸다고_알린다() -> None:
    """상한에 걸리면 범위 앞쪽이 통째로 빠진다. 이게 전부라고 말하면 안 된다."""
    events = [_event("camera-01", minute, minute % 2) for minute in range(10)]

    outcome = _build(events=events, scan_limit=3).search("오늘", limit=20)

    assert outcome.truncated is True


def test_결과가_limit보다_많으면_잘렸다고_알린다() -> None:
    events = [_event("camera-01", minute, minute) for minute in range(10)]

    outcome = _build(events=events).search("오늘", limit=3)

    assert len(outcome.hits) == 3
    assert outcome.truncated is True


def test_실제로_있는_스냅샷만_연결한다() -> None:
    """worker는 탐지 개수가 바뀔 때만 올린다. 없는 키를 걸면 깨진 이미지가 뜬다."""
    events = [_event("camera-01", 0, 1), _event("camera-01", 5, 2)]
    existing = build_snapshot_key("camera-01", events[0].captured_at)

    outcome = _build(events=events, snapshot_keys=[existing]).search("오늘", limit=20)

    by_id = {hit.event_id: hit for hit in outcome.hits}
    assert by_id["camera-01-0"].snapshot_key == existing
    assert by_id["camera-01-5"].snapshot_key is None
    assert outcome.snapshot_lookup_failed is False


def test_스냅샷_저장소가_죽어도_메타데이터는_돌려준다() -> None:
    """이미지를 못 붙였을 뿐 조회는 성공했다. 둘을 함께 잃으면 판단 근거가 없다."""
    events = [_event("camera-01", 0, 1)]

    outcome = _build(events=events, snapshot_fails=True).search("오늘", limit=20)

    assert len(outcome.hits) == 1
    assert outcome.hits[0].snapshot_key is None
    assert outcome.snapshot_lookup_failed is True


def test_식별된_학생과_그렇지_않은_인원을_구분해_담는다() -> None:
    """식별 생산자가 붙기 전에는 identified가 비고 전원이 미식별로 잡힌다."""
    events = [_event("camera-01", 0, 3, student_ids=("student-1",))]

    outcome = _build(events=events).search("오늘", limit=20)

    hit = outcome.hits[0]
    assert hit.detection_count == 3
    assert [student.student_id for student in hit.identified] == ["student-1"]
    assert hit.unidentified_count == 2


def test_프롬프트에_등록된_카메라를_넣어_준다() -> None:
    planner = FakePlanner(
        {"intent": "detection_search", "from": _FROM, "to": _TO},
    )

    _build(planner=planner, streams=[_stream("camera-07", "C305")]).search("오늘", limit=20)

    assert planner.last_prompt is not None
    assert "camera-07" in planner.last_prompt.system
    assert planner.last_prompt.question == "오늘"
    assert planner.last_prompt.now == _NOW


def test_모델이_규격을_벗어나면_한_번_더_시도한다() -> None:
    """작은 모델은 첫 응답이 깨져도 다시 물으면 붙는 일이 흔하다.

    한 번의 실패로 422를 돌려주면 사용자는 잘못이 없는데 질문을 고치라는 말을 듣는다.
    """
    valid = json.dumps(
        {
            "intent": "detection_search",
            "camera_id": None,
            "classroom_id": None,
            "from": _FROM,
            "to": _TO,
        }
    )
    planner = SequencePlanner("```json\n설명을 덧붙였습니다```", valid)

    outcome = _build(planner=planner).search("오늘", limit=20)

    assert outcome.query.from_at == datetime(2026, 8, 14, 0, 0, tzinfo=UTC)
    assert len(planner.prompts) == 2


def test_재시도할_때만_규격을_다시_못_박는다() -> None:
    """모델 원문은 되돌려 넣지 않는다. 같은 실수를 다시 읽게 만들 뿐이다."""
    valid = json.dumps({"intent": "detection_search", "from": _FROM, "to": _TO})
    planner = SequencePlanner("엉터리", valid)

    _build(planner=planner).search("오늘", limit=20)

    first, second = planner.prompts
    assert "직전 응답이 규격을 벗어났다" not in first.system
    assert "직전 응답이 규격을 벗어났다" in second.system
    assert "엉터리" not in second.system


def test_두_번_다_규격을_벗어나면_계획_오류를_던진다() -> None:
    planner = FakePlanner(None)

    with pytest.raises(LlmSearchPlanInvalidError):
        _build(planner=planner).search("오늘", limit=20)

    assert planner.calls == 2


def test_LLM에_닿지_못하면_재시도하지_않고_그대로_전달한다() -> None:
    """'조건을 못 만들었다'와 다르다. 질문을 고쳐도 해결되지 않는다.

    한 번 더 부르면 사용자를 두 배로 기다리게 할 뿐이다 — 이미 타임아웃을 다 쓴
    상황일 수 있다.
    """
    planner = FakePlanner(fails=True)

    with pytest.raises(LlmSearchPlannerUnavailableError):
        _build(planner=planner).search("오늘", limit=20)

    assert planner.calls == 1
