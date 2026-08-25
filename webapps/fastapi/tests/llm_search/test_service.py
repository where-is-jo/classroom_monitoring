"""계획을 실제 탐지 조회로 실행하는 계약.

LLM도 HTTP도 없이 돈다. 대역은 고정된 계획 문자열 하나를 돌려주고, 저장소는 기존
메모리 어댑터를 그대로 쓴다.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from datetime import UTC, date, datetime, timedelta

import pytest

from app.classrooms.adapters.memory_repository import InMemoryClassroomRepository
from app.classrooms.models import Classroom
from app.llm_search.errors import LlmSearchPlanInvalidError, LlmSearchPlannerUnavailableError
from app.llm_search.ports import PlanPrompt, QueryPlanner
from app.llm_search.service import LlmSearchService
from app.snapshots.errors import SnapshotStorageUnavailableError
from app.snapshots.models import build_snapshot_key
from app.snapshots.ports import ObjectContent, StoredObject
from app.snapshots.service import SnapshotService
from app.student_monitoring.adapters.memory_repository import MemoryDetectionEventRepository
from app.student_monitoring.models import Detection, DetectionEvent, FrameInfo
from app.students.adapters.memory import InMemoryStudentRepository
from app.students.models import Student
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


def _student(student_id: str, name: str) -> Student:
    """학생 원장 한 명. 이름으로 사람을 찾는 경로에만 쓴다."""
    return Student(
        id=student_id,
        student_number=student_id,
        name=name,
        birth_date=date(2008, 3, 1),
        classroom_name="A101",
        phone=None,
        guardian_phone="010-0000-0000",
        face_enrollment_id=None,
        face_registered=False,
        is_active=True,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _classroom(classroom_id: str, code: str, name: str) -> Classroom:
    return Classroom(
        id=classroom_id,
        code=code,
        name=name,
        location=code,
        is_active=True,
        created_at=_NOW,
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
    classrooms: list[Classroom] | None = None,
    snapshot_keys: list[str] | None = None,
    snapshot_fails: bool = False,
    scan_limit: int = 500,
    students: list[Student] | None = None,
) -> LlmSearchService:
    detections = MemoryDetectionEventRepository()
    for event in events or []:
        detections.save(event)

    stream_repository = MemoryVideoStreamRepository()
    for stream in streams if streams is not None else [_stream("camera-01", "A101")]:
        stream_repository.save(stream)

    classroom_repository = InMemoryClassroomRepository()
    for classroom in classrooms or []:
        classroom_repository.create_classroom(classroom)

    student_repository = InMemoryStudentRepository()
    for student in students or []:
        student_repository.create(student)

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
        classroom_repository,
        SnapshotService(FakeStorage(snapshot_keys, fails=snapshot_fails)),
        student_repository,
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


def test_강의실을_부르는_이름을_프롬프트에_함께_싣는다() -> None:
    """`classroom_id`가 UUID라 이름이 없으면 모델이 질문과 이을 근거가 없다.

    2026-08-23 실측: 목록에 UUID만 있는 상태에서 "A111 강의실에 오늘 몇 명
    있었어?"에 모델이 `classroom_id="A111"`을 냈고 0건이 나왔다.
    """
    planner = FakePlanner(
        {
            "intent": "detection_search",
            "camera_id": None,
            "classroom_id": None,
            "from": _FROM,
            "to": _TO,
            "limit": 20,
        }
    )

    _build(
        planner=planner,
        streams=[_stream("camera-01", "room-a101")],
        classrooms=[_classroom("room-a101", "A101", "1강의실")],
    ).search("오늘 누가 왔어?", limit=20)

    assert planner.last_prompt is not None
    assert "classroom_id=room-a101 (A101 1강의실)" in planner.last_prompt.system


def test_모델이_강의실_코드를_내도_같은_강의실을_찾는다() -> None:
    """프롬프트가 목록에 코드를 실어 주므로 보통은 UUID가 온다.

    그래도 **결과를 그것 하나에 걸지 않는다.** 코드를 그대로 받아도 등록을 되짚어
    같은 강의실을 찾는다. 이 방어층이 없으면 모델이 한 번 흔들릴 때마다 사용자는
    "등록되지 않은 강의실"이라는 틀린 안내를 받는다.
    """
    plan = {
        "intent": "detection_search",
        "camera_id": None,
        "classroom_id": "A101",  # UUID가 아니라 사람이 부르는 코드
        "from": _FROM,
        "to": _TO,
        "limit": 20,
    }

    outcome = _build(
        planner=FakePlanner(plan),
        events=[_event("camera-01", 0, 2)],
        streams=[_stream("camera-01", "room-a101")],
        classrooms=[_classroom("room-a101", "A101", "1강의실")],
    ).search("A101에 몇 명 있었어?", limit=20)

    assert [hit.event_id for hit in outcome.hits] == ["camera-01-0"]
    assert outcome.query.notes == ()


def test_대상을_사람이_읽는_이름으로_적는다() -> None:
    """화면이 UUID를 그대로 보여주면 어느 강의실인지 알 수 없다."""
    plan = {
        "intent": "detection_search",
        "camera_id": None,
        "classroom_id": "room-a101",
        "from": _FROM,
        "to": _TO,
        "limit": 20,
    }

    outcome = _build(
        planner=FakePlanner(plan),
        events=[_event("camera-01", 0, 2)],
        streams=[_stream("camera-01", "room-a101")],
        classrooms=[_classroom("room-a101", "A101", "1강의실")],
    ).search("A101에 몇 명 있었어?", limit=20)

    assert outcome.target_label == "A101 1강의실"
    assert outcome.hits[0].resolved_classroom_label == "A101 1강의실"
    # 식별자는 그대로 남는다. 호출자가 다른 API에 이어 쓰는 값이다.
    assert outcome.hits[0].resolved_classroom_id == "room-a101"


def test_강의실_등록을_찾지_못하면_식별자를_그대로_보여준다() -> None:
    """빈칸으로 두면 **어느 강의실인지 모른다는 사실 자체가 화면에서 사라진다.**"""
    plan = {
        "intent": "detection_search",
        "camera_id": None,
        "classroom_id": "room-a101",
        "from": _FROM,
        "to": _TO,
        "limit": 20,
    }

    outcome = _build(
        planner=FakePlanner(plan),
        events=[_event("camera-01", 0, 2)],
        streams=[_stream("camera-01", "room-a101")],
    ).search("A101에 몇 명 있었어?", limit=20)

    assert outcome.target_label == "강의실 room-a101"
    assert outcome.hits[0].resolved_classroom_label == "강의실 room-a101"


def test_없는_강의실을_물으면_들은_이름으로_안내한다() -> None:
    """등록되지 않은 곳은 정상적인 0건이다. **왜 0건인지**가 안내에 남아야 한다."""
    plan = {
        "intent": "detection_search",
        "camera_id": None,
        "classroom_id": "B203",
        "from": _FROM,
        "to": _TO,
        "limit": 20,
    }

    outcome = _build(
        planner=FakePlanner(plan),
        events=[_event("camera-01", 0, 2)],
        streams=[_stream("camera-01", "room-a101")],
        classrooms=[_classroom("room-a101", "A101", "1강의실")],
    ).search("B203에 몇 명 있었어?", limit=20)

    assert outcome.hits == ()
    assert outcome.target_label == "강의실 B203"
    assert any("B203" in note for note in outcome.query.notes)


def test_카메라를_콕_집으면_카메라_이름으로_적는다() -> None:
    plan = {
        "intent": "detection_search",
        "camera_id": "camera-01",
        "classroom_id": None,
        "from": _FROM,
        "to": _TO,
        "limit": 20,
    }

    outcome = _build(
        planner=FakePlanner(plan),
        events=[_event("camera-01", 0, 2)],
        streams=[_stream("camera-01", "room-a101")],
        classrooms=[_classroom("room-a101", "A101", "1강의실")],
    ).search("camera-01에 몇 명 있었어?", limit=20)

    assert outcome.target_label == "카메라 room-a101 카메라 (camera-01)"


def test_같은_강의실을_여러_카메라가_가리켜도_한_번만_조회한다() -> None:
    """카메라가 늘 때마다 강의실 조회가 늘면 검색 한 번의 왕복이 카메라 수만큼 는다."""

    class CountingClassrooms(InMemoryClassroomRepository):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def get_classroom(self, classroom_id: str) -> Classroom | None:
            self.calls += 1
            return super().get_classroom(classroom_id)

    classrooms = CountingClassrooms()
    classrooms.create_classroom(_classroom("room-a101", "A101", "1강의실"))

    detections = MemoryDetectionEventRepository()
    stream_repository = MemoryVideoStreamRepository()
    for stream in (_stream("camera-01", "room-a101"), _stream("camera-02", "room-a101")):
        stream_repository.save(stream)

    service = LlmSearchService(
        FakePlanner(
            {
                "intent": "detection_search",
                "camera_id": None,
                "classroom_id": None,
                "from": _FROM,
                "to": _TO,
                "limit": 20,
            }
        ),
        detections,
        stream_repository,
        classrooms,
        SnapshotService(FakeStorage()),
        InMemoryStudentRepository(),
        max_span_days=7,
        scan_limit=500,
        clock=lambda: _NOW,
    )
    service.search("오늘 누가 왔어?", limit=20)

    assert classrooms.calls == 1


def _person_plan(name: str, presence: str) -> dict[str, object]:
    return {
        "intent": "detection_search",
        "camera_id": None,
        "classroom_id": None,
        "from": _FROM,
        "to": _TO,
        "limit": 20,
        "person_name": name,
        "person_presence": presence,
    }


def test_사람을_말하지_않으면_인물_요약을_만들지_않는다() -> None:
    outcome = _build(events=[_event("camera-01", 0, 1)]).search("오늘", limit=20)

    assert outcome.person is None


def test_신원이_실린_탐지에서는_없는_사람_조건을_실제로_건다() -> None:
    """얼굴 인식이 붙어 `student_id`가 채워지면 같은 코드가 그대로 조건을 건다."""
    events = [
        _event("camera-01", 0, 2, student_ids=("student-1",)),
        _event("camera-01", 5, 3, student_ids=("student-2",)),
    ]

    outcome = _build(
        planner=FakePlanner(_person_plan("박무현", "absent")),
        events=events,
        students=[_student("student-1", "박무현"), _student("student-2", "김서아")],
    ).search("박무현 없는 스냅샷", limit=20)

    assert outcome.person is not None
    assert outcome.person.applied is True
    assert [hit.event_id for hit in outcome.hits] == ["camera-01-5"]


def test_있는_사람_조건은_반대로_고른다() -> None:
    """이름만 뽑고 방향을 버리면 두 질문이 서버에서 같아진다."""
    events = [
        _event("camera-01", 0, 2, student_ids=("student-1",)),
        _event("camera-01", 5, 3, student_ids=("student-2",)),
    ]

    outcome = _build(
        planner=FakePlanner(_person_plan("박무현", "present")),
        events=events,
        students=[_student("student-1", "박무현"), _student("student-2", "김서아")],
    ).search("박무현 나온 스냅샷", limit=20)

    assert [hit.event_id for hit in outcome.hits] == ["camera-01-0"]


def test_신원이_하나도_없으면_조건을_걸지_않고_알린다() -> None:
    """지금의 기본 상태다. 얼굴 인식이 없어 `Detection.student_id`가 비어 있다.

    `absent`를 그대로 걸면 전부 통과해 "확인했더니 없더라"로 보인다. 근거 없는
    답이라 걸지 않고, **걸지 못했다는 사실을 남긴다.**
    """
    events = [_event("camera-01", 0, 2), _event("camera-01", 5, 3)]

    outcome = _build(
        planner=FakePlanner(_person_plan("박무현", "absent")),
        events=events,
        students=[_student("student-1", "박무현")],
    ).search("박무현 없는 스냅샷", limit=20)

    assert outcome.person is not None
    assert outcome.person.applied is False
    assert outcome.person.identity_available is False
    assert len(outcome.hits) == 2
    assert any("얼굴 인식이 아직 연결되지 않아" in note for note in outcome.query.notes)
    assert "확인하지 못했고" in outcome.briefing


def test_명부에_없는_이름은_사유를_남기고_전체를_돌려준다() -> None:
    events = [_event("camera-01", 0, 2, student_ids=("student-1",))]

    outcome = _build(
        planner=FakePlanner(_person_plan("없는사람", "absent")),
        events=events,
        students=[_student("student-1", "박무현")],
    ).search("없는사람 없는 스냅샷", limit=20)

    assert outcome.person is not None
    assert outcome.person.student_id is None
    assert outcome.person.match_count == 0
    assert outcome.person.applied is False
    assert len(outcome.hits) == 1
    assert any("찾지 못해" in note for note in outcome.query.notes)


def test_동명이인이면_누구인지_정하지_않는다() -> None:
    """임의로 하나를 집는 것보다 "정하지 못했다"고 말하는 쪽이 맞다."""
    events = [_event("camera-01", 0, 2, student_ids=("student-1",))]

    outcome = _build(
        planner=FakePlanner(_person_plan("박무현", "present")),
        events=events,
        students=[_student("student-1", "박무현"), _student("student-2", "박무현")],
    ).search("박무현 있는 스냅샷", limit=20)

    assert outcome.person is not None
    assert outcome.person.student_id is None
    assert outcome.person.match_count == 2
    assert outcome.person.applied is False
    assert any("2명" in note for note in outcome.query.notes)
    assert "2명이라 누구인지 정하지 못했고" in outcome.briefing


def test_인물_조건은_변화_시점을_고른_뒤에_건다() -> None:
    """먼저 걸러 내면 남은 프레임들 사이에서 인원 변화를 다시 세게 된다.

    아래 이벤트는 1분과 2분이 같은 2명이라 변화 시점은 0·1·3분뿐이다. 인물 조건을
    먼저 걸었다면 2분(박무현 없음)이 "변화"로 살아나 결과에 끼어든다.
    """
    events = [
        _event("camera-01", 0, 1, student_ids=("student-1",)),
        _event("camera-01", 1, 2, student_ids=("student-1",)),
        _event("camera-01", 2, 2, student_ids=("student-2",)),
        _event("camera-01", 3, 3, student_ids=("student-2",)),
    ]

    outcome = _build(
        planner=FakePlanner(_person_plan("박무현", "absent")),
        events=events,
        students=[_student("student-1", "박무현"), _student("student-2", "김서아")],
    ).search("박무현 없는 스냅샷", limit=20)

    assert [hit.event_id for hit in outcome.hits] == ["camera-01-3"]


def test_브리핑에_기간과_대상과_건수를_함께_담는다() -> None:
    """화면이 이 문장들을 다시 조립하면 표기 규칙이 템플릿으로 샌다."""
    plan = {
        "intent": "detection_search",
        "camera_id": None,
        "classroom_id": "room-a101",
        "from": _FROM,
        "to": _TO,
        "limit": 20,
    }

    outcome = _build(
        planner=FakePlanner(plan),
        events=[_event("camera-01", 0, 2)],
        streams=[_stream("camera-01", "room-a101")],
        classrooms=[_classroom("room-a101", "A101", "1강의실")],
    ).search("오늘 A101", limit=20)

    assert "A101 1강의실에서 찾았어요" in outcome.briefing
    assert "총 1건의 결과가 있어요" in outcome.briefing
