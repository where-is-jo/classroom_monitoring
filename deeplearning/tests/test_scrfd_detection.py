import numpy as np

from deeplearning.scrfd_detection import (
    FaceCandidateStatus,
    ScrfdBoxTracker,
    ScrfdCandidateDetector,
)


class FakeScrfd:
    def __init__(
        self,
        detections: list[list[float]],
        landmarks: list[list[list[float]]] | None = None,
    ) -> None:
        self._detections = np.asarray(detections, dtype=np.float32)
        self._landmarks = np.asarray(
            landmarks
            or [
                [
                    [left + (right - left) * 0.3, top + (bottom - top) * 0.3],
                    [left + (right - left) * 0.7, top + (bottom - top) * 0.3],
                    [left + (right - left) * 0.5, top + (bottom - top) * 0.5],
                    [left + (right - left) * 0.35, top + (bottom - top) * 0.75],
                    [left + (right - left) * 0.65, top + (bottom - top) * 0.75],
                ]
                for left, top, right, bottom, _ in detections
            ],
            dtype=np.float32,
        )

    def detect(
        self, image: np.ndarray, *, max_num: int
    ) -> tuple[np.ndarray, np.ndarray]:
        del image, max_num
        return self._detections.copy(), self._landmarks.copy()


def test_낮은_confidence_후보는_확인_필요로_유지한다() -> None:
    detector = ScrfdCandidateDetector(
        FakeScrfd([[10, 10, 40, 50, 0.35], [50, 10, 90, 60, 0.8]]),
        candidate_threshold=0.25,
        face_threshold=0.6,
    )

    results = detector.detect(np.zeros((100, 100, 3), dtype=np.uint8))

    assert [item.status for item in results] == [
        FaceCandidateStatus.REVIEW,
        FaceCandidateStatus.FACE,
    ]


def test_타일과_전체_프레임의_중복_bbox를_하나로_합친다() -> None:
    detector = ScrfdCandidateDetector(FakeScrfd([[10, 10, 40, 50, 0.8]]))

    results = detector.detect_tiled(
        np.zeros((100, 100, 3), dtype=np.uint8), rows=1, columns=1
    )

    assert len(results) == 1


def test_bbox_밖으로_무너진_landmark_후보는_제거한다() -> None:
    detector = ScrfdCandidateDetector(
        FakeScrfd(
            [[10, 10, 40, 50, 0.8]],
            landmarks=[[[100, 100], [110, 100], [105, 110], [100, 120], [110, 120]]],
        )
    )

    results = detector.detect(np.zeros((100, 100, 3), dtype=np.uint8))

    assert results == ()


def test_짧은_검출_누락에는_기존_bbox를_유지한다() -> None:
    detector = ScrfdCandidateDetector(FakeScrfd([[10, 10, 40, 50, 0.8]]))
    tracker = ScrfdBoxTracker(stale_cycles=2)
    detection = detector.detect(np.zeros((100, 100, 3), dtype=np.uint8))

    first = tracker.update(detection)
    missed_once = tracker.update(())
    missed_twice = tracker.update(())
    expired = tracker.update(())

    assert len(first) == len(missed_once) == len(missed_twice) == 1
    assert first[0].track_id == missed_once[0].track_id
    assert expired == ()
