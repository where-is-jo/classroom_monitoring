from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from shared.types import CapturedFrame

from ..identity_handover import (
    IdentityHandoverResultHandler,
    IdentityHandoverRoute,
    RefreshingIdentityHandoverResultHandler,
    parse_identity_handover_routes,
)
from ..types import (
    Detection,
    EntryFaceObservation,
    EntryFaceObservationBatch,
    EntryIdentityProcessingStatus,
    EntryIdentityStatus,
    InferenceResult,
)

STARTED_AT = datetime(2026, 8, 22, 9, 0, tzinfo=UTC)
ROUTE = IdentityHandoverRoute("entry-camera", "classroom-cctv", (0.0, 0.0, 0.3, 1.0))


def captured(camera_id: str, seconds: float, sequence: int = 0) -> CapturedFrame:
    return CapturedFrame(
        camera_id=camera_id,
        frame=np.zeros((100, 200, 3), dtype=np.uint8),
        captured_at=STARTED_AT + timedelta(seconds=seconds),
        sequence=sequence,
    )


def face(
    track_id: str = "face-4",
    *,
    student_id: str | None = "student-001",
    similarity: float | None = 0.91,
    status: EntryIdentityStatus = EntryIdentityStatus.REGISTERED,
) -> EntryFaceObservation:
    return EntryFaceObservation(
        face_track_id=track_id,
        face_bbox=(60, 5, 140, 98),
        detection_confidence=0.95,
        identity_status=status,
        student_id=student_id,
        similarity=similarity,
        margin=0.3 if similarity is not None else None,
        quality=0.8,
        observation_count=4,
        rejected_reason=None,
    )


def batch(
    *observations: EntryFaceObservation,
    processing_status: EntryIdentityProcessingStatus = (
        EntryIdentityProcessingStatus.SUCCEEDED
    ),
) -> EntryFaceObservationBatch:
    return EntryFaceObservationBatch(
        frame_shape=(100, 200, 3),
        processing_status=processing_status,
        observations=observations,
    )


def person(track_id: str, bbox: tuple[int, int, int, int]) -> Detection:
    return Detection(0, "person", 0.9, bbox, track_id=track_id)


def result(*detections: Detection) -> InferenceResult:
    return InferenceResult((100, 200, 3), detections)


def handler() -> tuple[
    IdentityHandoverResultHandler,
    list[tuple[CapturedFrame, InferenceResult]],
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


def test_등록_얼굴을_CCTV_문_ROI의_신규_track에_인계한다() -> None:
    active, handled = handler()
    active.observe_entry(captured("entry-camera", 1), batch(face()))

    active(
        captured("classroom-cctv", 3),
        result(person("person-12", (0, 5, 50, 95))),
    )

    detection = handled[-1][1].detections[0]
    assert detection.student_id == "student-001"
    assert detection.identity_confidence == 0.91
    assert detection.track_id == "person-12"
    assert detection.face_bbox is None


def test_인계한_신원은_문_ROI를_벗어나도_같은_track에_유지한다() -> None:
    active, handled = handler()
    active.observe_entry(captured("entry-camera", 1), batch(face()))
    active(captured("classroom-cctv", 2), result(person("person-12", (0, 5, 50, 95))))

    active(
        captured("classroom-cctv", 6),
        result(person("person-12", (120, 20, 190, 100))),
    )

    assert handled[-1][1].detections[0].student_id == "student-001"


def test_ByteTrack_만료를_받으면_신원을_즉시_정리하고_새_인계를_허용한다() -> None:
    active, handled = handler()
    active.observe_entry(captured("entry-camera", 1), batch(face("face-1")))
    active(captured("classroom-cctv", 2), result(person("person-12", (0, 5, 50, 95))))

    active.expire_classroom_tracks("classroom-cctv", ("person-12",))
    active.observe_entry(captured("entry-camera", 3), batch(face("face-2")))
    active(captured("classroom-cctv", 4), result(person("person-13", (0, 5, 50, 95))))

    assert handled[-1][1].detections[0].student_id == "student-001"


def test_복수_등록_얼굴이면_추측해서_인계하지_않는다() -> None:
    active, handled = handler()
    active.observe_entry(
        captured("entry-camera", 1),
        batch(face("face-1"), face("face-2", student_id="student-002", similarity=0.9)),
    )

    active(captured("classroom-cctv", 2), result(person("person-12", (0, 5, 50, 95))))

    assert handled[-1][1].detections[0].student_id is None


def test_같은_학생의_얼굴_track이_바뀌어도_학생_후보는_하나로_본다() -> None:
    active, handled = handler()
    active.observe_entry(
        captured("entry-camera", 1),
        batch(face("face-1"), face("face-2")),
    )

    active(captured("classroom-cctv", 2), result(person("person-12", (0, 5, 50, 95))))

    assert handled[-1][1].detections[0].student_id == "student-001"


def test_문_ROI에_CCTV_track이_동시에_둘이면_인계하지_않는다() -> None:
    active, handled = handler()
    active.observe_entry(captured("entry-camera", 1), batch(face()))

    active(
        captured("classroom-cctv", 2),
        result(
            person("person-12", (0, 5, 50, 95)),
            person("person-13", (5, 5, 55, 95)),
        ),
    )

    assert all(item.student_id is None for item in handled[-1][1].detections)


def test_8초를_넘긴_CCTV_track에는_인계하지_않는다() -> None:
    active, handled = handler()
    active.observe_entry(captured("entry-camera", 1), batch(face()))

    active(captured("classroom-cctv", 9.1), result(person("person-12", (0, 5, 50, 95))))

    assert handled[-1][1].detections[0].student_id is None


def test_문_ROI_밖의_CCTV_track에는_인계하지_않는다() -> None:
    active, handled = handler()
    active.observe_entry(captured("entry-camera", 1), batch(face()))

    active(
        captured("classroom-cctv", 2),
        result(person("person-12", (120, 5, 190, 95))),
    )

    assert handled[-1][1].detections[0].student_id is None


def test_UNKNOWN과_분석_실패는_pending_신원이_되지_않는다() -> None:
    active, handled = handler()
    active.observe_entry(
        captured("entry-camera", 1),
        batch(
            face(
                status=EntryIdentityStatus.UNKNOWN,
                student_id=None,
                similarity=0.2,
            )
        ),
    )
    active.observe_entry(
        captured("entry-camera", 1.5),
        batch(processing_status=EntryIdentityProcessingStatus.ANALYZER_UNAVAILABLE),
    )

    active(captured("classroom-cctv", 2), result(person("person-12", (0, 5, 50, 95))))

    assert handled[-1][1].detections[0].student_id is None


def test_이미_소비한_face_track을_두번째_CCTV_track에_인계하지_않는다() -> None:
    active, handled = handler()
    active.observe_entry(captured("entry-camera", 1), batch(face("face-4")))
    active(captured("classroom-cctv", 2), result(person("person-12", (0, 5, 50, 95))))
    active.observe_entry(captured("entry-camera", 3), batch(face("face-4")))

    active(
        captured("classroom-cctv", 4),
        result(
            person("person-12", (120, 5, 190, 95)),
            person("person-22", (0, 5, 50, 95)),
        ),
    )

    by_track = {item.track_id: item for item in handled[-1][1].detections}
    assert by_track["person-12"].student_id == "student-001"
    assert by_track["person-22"].student_id is None


def test_설정_parser는_교실별_route_중복을_거부한다() -> None:
    value = (
        '[{"entry_camera_id":"entry-1","classroom_camera_id":"cctv",'
        '"classroom_entry_zone":[0,0,0.2,1]},'
        '{"entry_camera_id":"entry-2","classroom_camera_id":"cctv",'
        '"classroom_entry_zone":[0,0,0.2,1]}]'
    )

    with pytest.raises(ValueError, match="교실 camera_id"):
        parse_identity_handover_routes(value)


def test_동적_route는_카메라_역할이_바뀌면_적용하지_않는다() -> None:
    class Provider:
        def load(self) -> tuple[IdentityHandoverRoute, ...]:
            return (
                IdentityHandoverRoute(
                    "classroom-cctv",
                    "entry-camera",
                    (0.0, 0.0, 0.3, 1.0),
                ),
            )

    active = RefreshingIdentityHandoverResultHandler(
        (ROUTE,),
        provider=Provider(),
        inner=lambda _captured, _result: None,
        refresh_seconds=1,
        available_camera_ids=frozenset({"entry-camera", "classroom-cctv"}),
        entry_camera_ids=frozenset({"entry-camera"}),
        classroom_camera_ids=frozenset({"classroom-cctv"}),
    )

    # 역할 오류는 기존 route를 유지하며 호출 경로를 중단하지 않는다.
    active.observe_entry(captured("entry-camera", 1), batch(face()))
    enriched = active.enrich_classroom(
        captured("classroom-cctv", 2),
        result(person("person-12", (0, 5, 50, 95))),
    )

    assert enriched.detections[0].student_id == "student-001"
