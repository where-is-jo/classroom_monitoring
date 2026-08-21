import numpy as np

from deeplearning.cross_camera_tracking import (
    CrossCameraCalibration,
    CrossCameraTracker,
    IdentityPayload,
    TrackObservation,
)
from deeplearning.homecam_tracking import TrackIdentityStatus


def feature(index: int) -> np.ndarray:
    value = np.zeros(512, dtype=np.float32)
    value[index] = 1.0
    return value


def calibration() -> CrossCameraCalibration:
    square = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
    return CrossCameraCalibration(
        entry_resolution=(640, 480),
        classroom_resolution=(640, 480),
        entry_overlap_polygon=square,
        classroom_overlap_polygon=square,
        entry_correspondence_points=square,
        classroom_correspondence_points=square,
    )


def observation(
    camera_id: str,
    track_id: int,
    *,
    point: tuple[float, float] = (0.5, 0.5),
    timestamp: float = 1.0,
    vector: np.ndarray | None = None,
) -> TrackObservation:
    return TrackObservation(camera_id, track_id, point, timestamp, vector)


def registered(student_id: str = "student-1") -> IdentityPayload:
    return IdentityPayload(TrackIdentityStatus.REGISTERED, student_id)


def test_same_local_id_from_two_cameras_does_not_collide() -> None:
    tracker = CrossCameraTracker(calibration())
    entry = observation("entry", 1, vector=feature(0))
    classroom = observation("classroom", 1, vector=feature(0))

    entry_view = tracker.register_entry(entry, registered())
    tracker.match([entry], [classroom])
    classroom_view = tracker.lookup(classroom.key)

    assert classroom_view is not None
    assert classroom_view.global_track_id == entry_view.global_track_id


def test_one_person_handoff_succeeds() -> None:
    tracker = CrossCameraTracker(calibration())
    entry = observation("entry", 7, vector=feature(3))
    classroom = observation("classroom", 9, timestamp=1.1, vector=feature(3))
    tracker.register_entry(entry, registered())

    scores = tracker.match([entry], [classroom])

    assert scores[0].accepted is True
    assert tracker.lookup(classroom.key).identity.student_id == "student-1"  # type: ignore[union-attr]


def test_two_people_are_matched_one_to_one() -> None:
    tracker = CrossCameraTracker(calibration(), minimum_margin=0.05)
    entries = [
        observation("entry", 1, point=(0.2, 0.5), vector=feature(1)),
        observation("entry", 2, point=(0.8, 0.5), vector=feature(2)),
    ]
    classrooms = [
        observation("classroom", 20, point=(0.8, 0.5), vector=feature(2)),
        observation("classroom", 10, point=(0.2, 0.5), vector=feature(1)),
    ]
    for index, entry in enumerate(entries):
        tracker.register_entry(entry, registered(f"student-{index}"))

    scores = tracker.match(entries, classrooms)

    accepted = {
        (score.entry_track_id, score.classroom_track_id)
        for score in scores
        if score.accepted
    }
    assert accepted == {(1, 10), (2, 20)}


def test_close_competing_scores_are_deferred() -> None:
    tracker = CrossCameraTracker(calibration(), minimum_margin=0.10)
    entry = observation("entry", 1, vector=feature(1))
    classrooms = [
        observation("classroom", 10, vector=feature(1)),
        observation("classroom", 20, vector=feature(1)),
    ]
    tracker.register_entry(entry, registered())

    scores = tracker.match([entry], classrooms)

    assert scores[0].accepted is False
    assert scores[0].reason == "ambiguous"


def test_locked_identity_cannot_be_overwritten() -> None:
    tracker = CrossCameraTracker(calibration())
    entry = observation("entry", 1, vector=feature(1))
    first = tracker.register_entry(entry, registered("student-1"))

    second = tracker.register_entry(entry, registered("student-2"))

    assert second == first
    assert tracker.snapshot()["counts"]["identity_overwrite_blocked"] == 1  # type: ignore[index]


def test_mapping_expires_and_large_time_skew_does_not_match() -> None:
    tracker = CrossCameraTracker(calibration(), stale_seconds=2.0)
    entry = observation("entry", 1, timestamp=1.0, vector=feature(1))
    classroom = observation("classroom", 2, timestamp=2.0, vector=feature(1))
    tracker.register_entry(entry, registered())

    assert tracker.match([entry], [classroom]) == ()
    tracker.expire(now=3.1)

    assert tracker.lookup(entry.key) is None


def test_calibration_round_trip(tmp_path) -> None:
    original = calibration()
    path = tmp_path / "calibration.json"

    original.save(path)
    loaded = CrossCameraCalibration.load(path)

    assert loaded == original
    assert np.allclose(loaded.homography, np.eye(3), atol=1e-6)
