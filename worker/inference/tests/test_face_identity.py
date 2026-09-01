from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import numpy as np
import pytest
import requests

from shared.types import CapturedFrame

from ..face_identity import (
    EntryFaceProcessor,
    FaceIdentificationResponseError,
    FastAPIEntryIdentityEventHandler,
    HttpFaceIdentifier,
    build_entry_identity_event_payload,
)
from ..types import (
    EntryFaceObservationBatch,
    EntryIdentityProcessingStatus,
    EntryIdentityStatus,
)


class FakeResponse:
    def __init__(self, payload: object, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            response = requests.Response()
            response.status_code = self.status_code
            raise requests.HTTPError(response=response)

    def json(self) -> object:
        return self._payload


class InvalidJsonResponse(FakeResponse):
    def json(self) -> object:
        raise ValueError("invalid json")


def captured() -> CapturedFrame:
    return CapturedFrame(
        camera_id="entry-camera",
        frame=np.full((120, 160, 3), 128, dtype=np.uint8),
        captured_at=datetime(2026, 8, 22, 9, 0, 0, 123000, tzinfo=UTC),
        sequence=7,
    )


def observation(
    *,
    status: str = "REGISTERED",
    student_id: str | None = "student-001",
    similarity: float | None = 0.86,
    margin: float | None = 0.31,
) -> dict[str, object]:
    return {
        "face_track_id": "face-3",
        "face_bbox": [40, 20, 80, 65],
        "detection_confidence": 0.94,
        "identity_status": status,
        "student_id": student_id,
        "similarity": similarity,
        "margin": margin,
        "quality": 0.81,
        "observation_count": 4,
        "rejected_reason": None,
    }


def identifier_with(payload: object) -> tuple[HttpFaceIdentifier, list[dict[str, Any]]]:
    requests_seen: list[dict[str, Any]] = []

    def post(url: str, **kwargs: Any) -> FakeResponse:
        requests_seen.append({"url": url, **kwargs})
        return FakeResponse(payload)

    return (
        HttpFaceIdentifier(
            "http://deeplearning:8100",
            timeout_seconds=2,
            jpeg_quality=90,
            post=post,  # type: ignore[arg-type]
        ),
        requests_seen,
    )


def test_사람_bbox_없이_등록_얼굴_관측을_읽는다() -> None:
    identifier, requests_seen = identifier_with(
        {"observations": [observation()]}
    )

    parsed = identifier.identify(captured())

    assert parsed[0].identity_status is EntryIdentityStatus.REGISTERED
    assert parsed[0].student_id == "student-001"
    assert parsed[0].similarity == 0.86
    headers = requests_seen[0]["headers"]
    assert headers["X-Camera-ID"] == "entry-camera"
    assert "X-Person-Bboxes" not in headers
    assert requests_seen[0]["data"] != captured().frame.tobytes()


@pytest.mark.parametrize("status", ["UNKNOWN", "UNCERTAIN"])
def test_미식별_상태에는_학생_ID를_허용하지_않는다(status: str) -> None:
    identifier, _ = identifier_with(
        {
            "observations": [
                observation(
                    status=status,
                    student_id=None,
                    similarity=None,
                    margin=None,
                )
            ]
        }
    )

    parsed = identifier.identify(captured())

    assert parsed[0].student_id is None
    assert parsed[0].identity_status.value == status


def test_응답에_embedding이나_정의되지_않은_필드가_있으면_거부한다() -> None:
    item = observation()
    item["embedding"] = [0.1]
    identifier, _ = identifier_with({"observations": [item]})

    with pytest.raises(FaceIdentificationResponseError):
        identifier.identify(captured())


def test_REGISTERED_응답에_거절_사유가_있으면_거부한다() -> None:
    item = observation()
    item["rejected_reason"] = "threshold"
    identifier, _ = identifier_with({"observations": [item]})

    with pytest.raises(FaceIdentificationResponseError):
        identifier.identify(captured())


def test_얼굴_서비스_장애를_저장_가능한_처리_상태로_바꾼다() -> None:
    def post(url: str, **kwargs: Any) -> FakeResponse:
        del url, kwargs
        raise requests.ConnectionError("down")

    processor = EntryFaceProcessor(
        HttpFaceIdentifier(
            "http://deeplearning:8100",
            timeout_seconds=2,
            jpeg_quality=90,
            post=post,  # type: ignore[arg-type]
        )
    )

    batch = processor.process(captured())

    assert batch.processing_status is EntryIdentityProcessingStatus.ANALYZER_UNAVAILABLE
    assert batch.observations == ()


def test_잘못된_응답을_별도_처리_상태로_남긴다() -> None:
    identifier, _ = identifier_with({"observations": "invalid"})

    batch = EntryFaceProcessor(identifier).process(captured())

    assert batch.processing_status is EntryIdentityProcessingStatus.INVALID_RESPONSE


def test_JSON_파싱_실패도_서비스_장애가_아닌_응답_오류로_남긴다() -> None:
    def post(url: str, **kwargs: Any) -> InvalidJsonResponse:
        del url, kwargs
        return InvalidJsonResponse(None)

    identifier = HttpFaceIdentifier(
        "http://deeplearning:8100",
        timeout_seconds=2,
        jpeg_quality=90,
        post=post,  # type: ignore[arg-type]
    )

    batch = EntryFaceProcessor(identifier).process(captured())

    assert batch.processing_status is EntryIdentityProcessingStatus.INVALID_RESPONSE


def test_입구_이벤트_ID와_비식별_저장_payload를_만든다() -> None:
    identifier, _ = identifier_with({"observations": [observation()]})
    batch = EntryFaceProcessor(identifier).process(captured())

    payload = build_entry_identity_event_payload(captured(), batch)

    assert payload["event_id"] == "entry-camera-1787389200123-7-entry-face"
    assert payload["processing_status"] == "SUCCEEDED"
    assert "image" not in str(payload).lower()
    assert "embedding" not in str(payload).lower()


def test_저장_실패가_메모리_인계를_막지_않는다() -> None:
    observed: list[EntryFaceObservationBatch] = []

    def post(url: str, **kwargs: Any) -> FakeResponse:
        del url, kwargs
        raise requests.ConnectionError("down")

    handler = FastAPIEntryIdentityEventHandler(
        "http://fastapi:8000",
        inner=lambda _captured, batch: observed.append(batch),
        max_retries=0,
        post=post,  # type: ignore[arg-type]
    )
    batch = EntryFaceObservationBatch(
        frame_shape=captured().frame.shape,
        processing_status=EntryIdentityProcessingStatus.INVALID_RESPONSE,
        observations=(),
    )

    handler(captured(), batch)

    assert observed == [batch]
