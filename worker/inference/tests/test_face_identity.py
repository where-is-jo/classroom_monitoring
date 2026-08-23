from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import numpy as np
import pytest
import requests
from shared.types import CapturedFrame

from ..face_identity import (
    FaceIdentificationError,
    FaceIdentityResultHandler,
    HttpFaceIdentifier,
)
from ..types import Detection, InferenceResult


class FakeResponse:
    def __init__(
        self,
        payload: object,
        *,
        status_code: int = 200,
    ) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            response = requests.Response()
            response.status_code = self.status_code
            raise requests.HTTPError(response=response)

    def json(self) -> object:
        return self._payload


def captured() -> CapturedFrame:
    return CapturedFrame(
        camera_id="entry-camera",
        frame=np.full((120, 160, 3), 128, dtype=np.uint8),
        captured_at=datetime(2026, 8, 22, tzinfo=UTC),
        sequence=7,
    )


def inference_result() -> InferenceResult:
    return InferenceResult(
        frame_shape=(120, 160, 3),
        detections=(
            Detection(67, "cell phone", 0.7, (5, 5, 20, 20)),
            Detection(0, "person", 0.9, (20, 10, 120, 115)),
        ),
    )


def test_등록_학생_식별을_사람_탐지에_보강한다() -> None:
    requests_seen: list[dict[str, Any]] = []

    def post(url: str, **kwargs: Any) -> FakeResponse:
        requests_seen.append({"url": url, **kwargs})
        return FakeResponse(
            {
                "identities": [
                    {
                        "person_index": 0,
                        "face_bbox": [40, 20, 80, 65],
                        "track_id": "face-3",
                        "student_id": "student-001",
                        "identity_confidence": 0.86,
                    }
                ]
            }
        )

    identifier = HttpFaceIdentifier(
        "http://deeplearning:8100",
        timeout_seconds=2,
        jpeg_quality=90,
        post=post,  # type: ignore[arg-type]
    )

    result = identifier.enrich(captured(), inference_result())

    person = result.detections[1]
    assert person.student_id == "student-001"
    assert person.identity_confidence == 0.86
    assert person.face_bbox == (40, 20, 80, 65)
    assert person.track_id == "face-3"
    assert result.detections[0].student_id is None
    assert requests_seen[0]["headers"]["X-Camera-ID"] == "entry-camera"
    assert requests_seen[0]["headers"]["X-Person-Bboxes"] == "[[20,10,120,115]]"
    assert requests_seen[0]["content"] != captured().frame.tobytes()


def test_미확정_얼굴은_track만_보강하고_학생을_붙이지_않는다() -> None:
    def post(url: str, **kwargs: Any) -> FakeResponse:
        del url, kwargs
        return FakeResponse(
            {
                "identities": [
                    {
                        "person_index": 0,
                        "face_bbox": [40, 20, 80, 65],
                        "track_id": "face-4",
                        "student_id": None,
                        "identity_confidence": None,
                    }
                ]
            }
        )

    result = HttpFaceIdentifier(
        "http://deeplearning:8100",
        timeout_seconds=2,
        jpeg_quality=90,
        post=post,  # type: ignore[arg-type]
    ).enrich(captured(), inference_result())

    person = result.detections[1]
    assert person.student_id is None
    assert person.identity_confidence is None
    assert person.face_bbox is None
    assert person.track_id == "face-4"


def test_사람_ByteTrack_ID를_얼굴_track으로_덮어쓰지_않는다() -> None:
    def post(url: str, **kwargs: Any) -> FakeResponse:
        del url, kwargs
        return FakeResponse(
            {
                "identities": [
                    {
                        "person_index": 0,
                        "face_bbox": [40, 20, 80, 65],
                        "track_id": "face-4",
                        "student_id": "student-001",
                        "identity_confidence": 0.9,
                    }
                ]
            }
        )

    original = inference_result()
    tracked = InferenceResult(
        original.frame_shape,
        (
            original.detections[0],
            Detection(
                0,
                "person",
                0.9,
                (20, 10, 120, 115),
                track_id="person-7",
            ),
        ),
    )

    enriched = HttpFaceIdentifier(
        "http://deeplearning:8100",
        timeout_seconds=2,
        jpeg_quality=90,
        post=post,  # type: ignore[arg-type]
    ).enrich(captured(), tracked)

    assert enriched.detections[1].track_id == "person-7"
    assert enriched.detections[1].student_id == "student-001"


def test_중복된_사람_응답은_거부한다() -> None:
    item = {
        "person_index": 0,
        "face_bbox": [40, 20, 80, 65],
        "track_id": "face-3",
        "student_id": "student-001",
        "identity_confidence": 0.86,
    }

    with pytest.raises(FaceIdentificationError):
        HttpFaceIdentifier._parse_matches({"identities": [item, item]}, person_count=1)


def test_얼굴_서비스_실패는_원래_탐지_전송을_막지_않는다() -> None:
    class FailingIdentifier:
        def enrich(
            self, captured_frame: CapturedFrame, result: InferenceResult
        ) -> InferenceResult:
            del captured_frame, result
            raise FaceIdentificationError("서비스 중단")

    handled: list[InferenceResult] = []
    handler = FaceIdentityResultHandler(
        FailingIdentifier(),  # type: ignore[arg-type]
        camera_ids=frozenset({"entry-camera"}),
        inner=lambda captured_frame, result: handled.append(result),
    )
    original = inference_result()

    handler(captured(), original)

    assert handled == [original]


def test_대상이_아닌_카메라는_얼굴_서비스를_호출하지_않는다() -> None:
    class UnexpectedIdentifier:
        def enrich(
            self, captured_frame: CapturedFrame, result: InferenceResult
        ) -> InferenceResult:
            del captured_frame, result
            raise AssertionError("좌석 카메라에서 얼굴 식별을 호출하면 안 됩니다.")

    handled: list[InferenceResult] = []
    handler = FaceIdentityResultHandler(
        UnexpectedIdentifier(),  # type: ignore[arg-type]
        camera_ids=frozenset({"entry-camera"}),
        inner=lambda captured_frame, result: handled.append(result),
    )
    seat_camera = CapturedFrame(
        camera_id="seat-camera",
        frame=captured().frame,
        captured_at=captured().captured_at,
        sequence=8,
    )
    original = inference_result()

    handler(seat_camera, original)

    assert handled == [original]
