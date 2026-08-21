from dataclasses import dataclass

from deeplearning.face_identity import IdentityStatus
from deeplearning.homecam_tracking import (
    PersonTrack,
    PersonTrackIdentityStore,
    TrackIdentityStatus,
    associate_faces_to_people,
)


@dataclass(frozen=True)
class Face:
    bbox: tuple[int, int, int, int]
    student_id: str | None = "student-1"
    status: IdentityStatus = IdentityStatus.REGISTERED
    detection_confidence: float = 0.9
    similarity: float = 0.8
    margin: float = 0.2
    quality: float = 0.8


def test_face_is_assigned_to_one_enclosing_person() -> None:
    people = [PersonTrack(7, (0, 0, 200, 400), 0.9)]
    faces = [Face((50, 30, 110, 100))]

    associations = associate_faces_to_people(people, faces)

    assert associations[0].person_track_id == 7
    assert associations[0].reason == "matched"


def test_multiple_faces_in_one_person_are_rejected() -> None:
    people = [PersonTrack(7, (0, 0, 200, 400), 0.9)]
    faces = [Face((20, 20, 60, 70)), Face((100, 20, 140, 70))]

    associations = associate_faces_to_people(people, faces)

    assert {item.reason for item in associations} == {"multiple_faces"}
    assert all(item.person_track_id is None for item in associations)


def test_ambiguous_person_is_marked_uncertain() -> None:
    person = PersonTrack(7, (0, 0, 200, 400), 0.9)
    faces = [Face((20, 20, 60, 70)), Face((100, 20, 140, 70))]
    associations = associate_faces_to_people([person], faces)
    store = PersonTrackIdentityStore(minimum_observations=2, history_size=4)

    result = store.update([person], faces, associations)[0]

    assert result.status is TrackIdentityStatus.UNCERTAIN


def test_identity_is_confirmed_and_retained_without_face() -> None:
    person = PersonTrack(3, (0, 0, 100, 200), 0.9)
    face = Face((20, 20, 60, 70))
    association = associate_faces_to_people([person], [face])
    store = PersonTrackIdentityStore(minimum_observations=2, history_size=4)

    store.update([person], [face], association, now=1.0)
    confirmed = store.update([person], [face], association, now=2.5)[0]
    retained = store.update([person], [], [], now=3.0)[0]

    assert confirmed.status is TrackIdentityStatus.REGISTERED
    assert confirmed.student_id == "student-1"
    assert retained.student_id == "student-1"
    assert store.confirmation_durations == [1.5]


def test_conflicting_registered_identity_does_not_replace_locked_name() -> None:
    person = PersonTrack(3, (0, 0, 100, 200), 0.9)
    store = PersonTrackIdentityStore(minimum_observations=2, history_size=2)
    first = Face((20, 20, 60, 70), student_id="student-1")
    second = Face((20, 20, 60, 70), student_id="student-2")

    for now in (1.0, 2.0):
        store.update(
            [person], [first], associate_faces_to_people([person], [first]), now=now
        )
    for now in (3.0, 4.0):
        result = store.update(
            [person], [second], associate_faces_to_people([person], [second]), now=now
        )[0]

    assert result.student_id == "student-1"
    assert store.identity_switch_count >= 1


def test_track_identity_expires_after_stale_frame_limit() -> None:
    person = PersonTrack(3, (0, 0, 100, 200), 0.9)
    store = PersonTrackIdentityStore(
        minimum_observations=1,
        history_size=1,
        stale_frames=2,
    )
    face = Face((20, 20, 60, 70))
    store.update([person], [face], associate_faces_to_people([person], [face]))

    store.update([], [], [])
    store.update([], [], [])
    assert store.active_track_ids == frozenset({3})
    store.update([], [], [])

    assert store.active_track_ids == frozenset()
