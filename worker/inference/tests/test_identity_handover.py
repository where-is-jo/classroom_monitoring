from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from shared.types import CapturedFrame

from ..identity_handover import (
    HttpIdentityHandoverRouteProvider,
    IdentityHandoverResultHandler,
    IdentityHandoverRoute,
    RefreshingIdentityHandoverResultHandler,
    parse_identity_handover_routes,
)
from ..types import Detection, InferenceResult

STARTED_AT = datetime(2026, 8, 22, 9, 0, tzinfo=UTC)
ROUTE = IdentityHandoverRoute("entry-camera", "classroom-cctv", (0.0, 0.0, 0.3, 1.0))


def captured(camera_id: str, seconds: float, sequence: int = 0) -> CapturedFrame:
    return CapturedFrame(
        camera_id=camera_id,
        frame=np.zeros((100, 200, 3), dtype=np.uint8),
        captured_at=STARTED_AT + timedelta(seconds=seconds),
        sequence=sequence,
    )


def person(
    track_id: str,
    bbox: tuple[int, int, int, int],
    *,
    student_id: str | None = None,
    identity_confidence: float | None = None,
) -> Detection:
    return Detection(
        0,
        "person",
        0.9,
        bbox,
        student_id=student_id,
        identity_confidence=identity_confidence,
        track_id=track_id,
    )


def result(*detections: Detection) -> InferenceResult:
    return InferenceResult((100, 200, 3), detections)


def handler() -> tuple[
    IdentityHandoverResultHandler, list[tuple[CapturedFrame, InferenceResult]]
]:
    handled: list[tuple[CapturedFrame, InferenceResult]] = []
    active = IdentityHandoverResultHandler(
        (ROUTE,),
        inner=lambda frame, value: handled.append((frame, value)),
        maximum_delay_seconds=8,
        clock_skew_seconds=0.5,
        track_stale_seconds=30,
        minimum_identity_confidence=0.6,
    )
    return active, handled


def test_입구_학생을_문_영역의_신규_CCTV_track에_인계한다() -> None:
    active, handled = handler()
    active(
        captured("entry-camera", 1),
        result(
            person(
                "person-4",
                (60, 5, 140, 98),
                student_id="student-001",
                identity_confidence=0.91,
            )
        ),
    )

    active(
        captured("classroom-cctv", 3),
        result(person("person-12", (0, 5, 50, 95))),
    )

    cctv_detection = handled[-1][1].detections[0]
    assert cctv_detection.student_id == "student-001"
    assert cctv_detection.identity_confidence == 0.91
    assert cctv_detection.track_id == "person-12"
    assert cctv_detection.face_bbox is None


def test_인계한_신원은_문_영역을_벗어나_좌석까지_같은_track에_유지한다() -> None:
    active, handled = handler()
    active(
        captured("entry-camera", 1),
        result(
            person(
                "person-4",
                (60, 5, 140, 98),
                student_id="student-001",
                identity_confidence=0.91,
            )
        ),
    )
    active(
        captured("classroom-cctv", 3),
        result(person("person-12", (0, 5, 50, 95))),
    )

    active(
        captured("classroom-cctv", 6, sequence=1),
        result(person("person-12", (120, 30, 190, 100))),
    )

    seated = handled[-1][1].detections[0]
    assert seated.student_id == "student-001"
    assert seated.identity_confidence == 0.91


def test_처리_순서가_뒤집혀도_촬영시각으로_다음_CCTV_프레임부터_인계한다() -> None:
    active, handled = handler()
    active(
        captured("classroom-cctv", 3),
        result(person("person-12", (0, 5, 50, 95))),
    )
    assert handled[-1][1].detections[0].student_id is None

    active(
        captured("entry-camera", 1),
        result(
            person(
                "person-4",
                (60, 5, 140, 98),
                student_id="student-001",
                identity_confidence=0.91,
            )
        ),
    )
    active(
        captured("classroom-cctv", 4, sequence=1),
        result(person("person-12", (20, 5, 80, 95))),
    )

    assert handled[-1][1].detections[0].student_id == "student-001"


def test_문_영역_밖에서_생긴_track에는_신원을_붙이지_않는다() -> None:
    active, handled = handler()
    active(
        captured("entry-camera", 1),
        result(
            person(
                "person-4",
                (60, 5, 140, 98),
                student_id="student-001",
                identity_confidence=0.91,
            )
        ),
    )

    active(
        captured("classroom-cctv", 3),
        result(person("person-12", (120, 5, 190, 95))),
    )

    assert handled[-1][1].detections[0].student_id is None


def test_문_영역_밖에서_생긴_track이_안으로_진입하면_신원을_인계한다() -> None:
    active, handled = handler()
    active(
        captured("entry-camera", 1),
        result(
            person(
                "person-4",
                (60, 5, 140, 98),
                student_id="student-001",
                identity_confidence=0.91,
            )
        ),
    )
    active(
        captured("classroom-cctv", 2),
        result(person("person-12", (120, 5, 190, 95))),
    )

    active(
        captured("classroom-cctv", 3, sequence=1),
        result(person("person-12", (0, 5, 50, 95))),
    )

    entered = handled[-1][1].detections[0]
    assert entered.track_id == "person-12"
    assert entered.student_id == "student-001"
    assert entered.identity_confidence == 0.91


def test_인계된_track이_문_영역에_재진입해도_다른_신원으로_바꾸지_않는다() -> None:
    active, handled = handler()
    active(
        captured("entry-camera", 1),
        result(
            person(
                "person-4",
                (60, 5, 140, 98),
                student_id="student-001",
                identity_confidence=0.91,
            )
        ),
    )
    active(
        captured("classroom-cctv", 2),
        result(person("person-12", (0, 5, 50, 95))),
    )
    active(
        captured("classroom-cctv", 3, sequence=1),
        result(person("person-12", (120, 5, 190, 95))),
    )
    active(
        captured("entry-camera", 4, sequence=1),
        result(
            person(
                "person-5",
                (60, 5, 140, 98),
                student_id="student-002",
                identity_confidence=0.94,
            )
        ),
    )

    active(
        captured("classroom-cctv", 5, sequence=2),
        result(person("person-12", (0, 5, 50, 95))),
    )

    reentered = handled[-1][1].detections[0]
    assert reentered.student_id == "student-001"
    assert reentered.identity_confidence == 0.91


def test_입구_track_ID가_바뀌어도_같은_학생을_두_CCTV_track에_인계하지_않는다() -> None:
    active, handled = handler()
    active(
        captured("entry-camera", 1),
        result(
            person(
                "face-12",
                (60, 5, 140, 98),
                student_id="student-001",
                identity_confidence=0.91,
            )
        ),
    )
    active(
        captured("classroom-cctv", 2),
        result(person("person-5", (0, 5, 50, 95))),
    )
    assert handled[-1][1].detections[0].student_id == "student-001"

    # 같은 실제 사람이 다음 프레임에서 ByteTrack ID를 받아도 새 입장으로 보지 않는다.
    active(
        captured("entry-camera", 3, sequence=1),
        result(
            person(
                "person-12",
                (60, 5, 140, 98),
                student_id="student-001",
                identity_confidence=0.93,
            )
        ),
    )
    active(
        captured("classroom-cctv", 4, sequence=1),
        result(
            person("person-5", (120, 5, 190, 95)),
            person("person-22", (0, 5, 50, 95)),
        ),
    )

    by_track = {d.track_id: d for d in handled[-1][1].detections}
    assert by_track["person-5"].student_id == "student-001"
    assert by_track["person-22"].student_id is None


def test_후보_학생이_둘이면_가까운_track에_추측해_붙이지_않는다() -> None:
    active, handled = handler()
    for index, student_id in enumerate(("student-001", "student-002"), start=1):
        active(
            captured("entry-camera", index),
            result(
                person(
                    f"person-{index}",
                    (60, 5, 140, 98),
                    student_id=student_id,
                    identity_confidence=0.9,
                )
            ),
        )

    active(
        captured("classroom-cctv", 3),
        result(person("person-12", (0, 5, 50, 95))),
    )

    assert handled[-1][1].detections[0].student_id is None


def test_모호한_후보가_만료되어_하나만_남으면_다시_인계한다() -> None:
    active, handled = handler()
    active(
        captured("entry-camera", 1),
        result(
            person(
                "person-1",
                (60, 5, 140, 98),
                student_id="student-001",
                identity_confidence=0.9,
            )
        ),
    )
    active(
        captured("entry-camera", 4),
        result(
            person(
                "person-2",
                (60, 5, 140, 98),
                student_id="student-002",
                identity_confidence=0.92,
            )
        ),
    )
    active(
        captured("classroom-cctv", 5),
        result(person("person-12", (0, 5, 50, 95))),
    )
    assert handled[-1][1].detections[0].student_id is None

    # 첫 후보만 8초 창을 벗어났다. 새 track이나 새 얼굴 결과가 없어도 남은
    # 유일 후보를 기존 CCTV track에 다시 매칭해야 한다.
    active(
        captured("classroom-cctv", 10),
        result(person("person-12", (20, 5, 70, 95))),
    )

    assert handled[-1][1].detections[0].student_id == "student-002"
    assert handled[-1][1].detections[0].identity_confidence == 0.92


def test_문_영역에_신규_track이_둘이면_인계하지_않는다() -> None:
    active, handled = handler()
    active(
        captured("entry-camera", 1),
        result(
            person(
                "person-4",
                (60, 5, 140, 98),
                student_id="student-001",
                identity_confidence=0.91,
            )
        ),
    )

    active(
        captured("classroom-cctv", 3),
        result(
            person("person-12", (0, 5, 30, 95)),
            person("person-13", (25, 5, 55, 95)),
        ),
    )

    assert all(item.student_id is None for item in handled[-1][1].detections)


def test_JSON_route를_검증해_읽는다() -> None:
    routes = parse_identity_handover_routes(
        '[{"entry_camera_id":"entry-camera","classroom_camera_id":"classroom-cctv",'
        '"classroom_entry_zone":[0.0,0.1,0.3,1.0]}]'
    )

    assert routes[0].classroom_entry_zone == (0.0, 0.1, 0.3, 1.0)


def test_교실_camera에_route를_두_개_연결할_수_없다() -> None:
    with pytest.raises(ValueError, match="하나만"):
        parse_identity_handover_routes(
            '[{"entry_camera_id":"entry-a","classroom_camera_id":"classroom-cctv",'
            '"classroom_entry_zone":[0,0,0.3,1]},'
            '{"entry_camera_id":"entry-b","classroom_camera_id":"classroom-cctv",'
            '"classroom_entry_zone":[0,0,0.3,1]}]'
        )


def test_track_stale은_시간창과_시각오차의_합보다_길어야_한다() -> None:
    with pytest.raises(ValueError, match="시각 오차"):
        IdentityHandoverResultHandler(
            (ROUTE,),
            inner=lambda frame, value: None,
            maximum_delay_seconds=8,
            clock_skew_seconds=2,
            track_stale_seconds=9,
        )


class FakeRouteResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {
            "items": [
                {
                    "entry_camera_id": "entry-camera",
                    "classroom_camera_id": "classroom-cctv",
                    "classroom_entry_zone": [0.0, 0.0, 0.3, 1.0],
                }
            ]
        }


def test_FastAPI_관리_화면의_route를_읽는다() -> None:
    requests_seen: list[tuple[str, float]] = []

    def get(url: str, *, timeout: float) -> FakeRouteResponse:
        requests_seen.append((url, timeout))
        return FakeRouteResponse()

    routes = HttpIdentityHandoverRouteProvider(
        "http://fastapi:8001/",
        timeout_seconds=2,
        get=get,  # type: ignore[arg-type]
    ).load()

    assert requests_seen == [
        ("http://fastapi:8001/internal/identity-handover-routes", 2)
    ]
    assert routes == (ROUTE,)


def test_관리_화면_route를_실행_중에_반영한다() -> None:
    class Provider:
        def load(self) -> tuple[IdentityHandoverRoute, ...]:
            return (ROUTE,)

    handled: list[tuple[CapturedFrame, InferenceResult]] = []
    active = RefreshingIdentityHandoverResultHandler(
        (),
        provider=Provider(),
        inner=lambda frame, value: handled.append((frame, value)),
        refresh_seconds=5,
        maximum_delay_seconds=8,
        clock_skew_seconds=0.5,
        track_stale_seconds=30,
        monotonic=lambda: 0,
    )
    active(
        captured("entry-camera", 1),
        result(
            person(
                "person-4",
                (60, 5, 140, 98),
                student_id="student-001",
                identity_confidence=0.91,
            )
        ),
    )
    active(
        captured("classroom-cctv", 3),
        result(person("person-12", (0, 5, 50, 95))),
    )

    assert handled[-1][1].detections[0].student_id == "student-001"
