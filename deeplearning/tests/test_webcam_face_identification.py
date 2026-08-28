from __future__ import annotations

from typing import Any

import pytest

from deeplearning.training.webcam_face_identification import (
    AdaFaceIdentificationClient,
    parse_observations,
)


class Response:
    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


class Session:
    def __init__(self, ready: dict[str, Any]) -> None:
        self.ready = ready

    def get(self, *_args: Any, **_kwargs: Any) -> Response:
        return Response(self.ready)


def test_adaface와_갤러리가_준비된_경우에만_통과한다() -> None:
    client = AdaFaceIdentificationClient(
        "http://localhost:8100",
        camera_id="notebook-webcam",
        session=Session(
            {
                "status": "ready",
                "face_identification": "ready",
                "active_face_model": "adaface",
                "missing_gallery_entries": "0",
            }
        ),  # type: ignore[arg-type]
    )

    assert client.ensure_adaface_ready()["active_face_model"] == "adaface"


@pytest.mark.parametrize("model", ["arcface", None])
def test_adaface가_아니면_웹캠_검증을_시작하지_않는다(model: str | None) -> None:
    client = AdaFaceIdentificationClient(
        "http://localhost:8100",
        camera_id="notebook-webcam",
        session=Session(
            {
                "status": "ready",
                "face_identification": "ready",
                "active_face_model": model,
                "missing_gallery_entries": "0",
            }
        ),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="AdaFace"):
        client.ensure_adaface_ready()


def test_얼굴_식별_응답을_화면_표시용_값으로_변환한다() -> None:
    observations = parse_observations(
        {
            "observations": [
                {
                    "face_bbox": [10, 20, 110, 160],
                    "identity_status": "REGISTERED",
                    "student_id": "student-1",
                    "similarity": 0.81,
                    "observation_count": 4,
                }
            ]
        }
    )

    assert observations[0].bbox == (10, 20, 110, 160)
    assert observations[0].student_id == "student-1"
    assert observations[0].similarity == pytest.approx(0.81)


def test_잘못된_얼굴_식별_응답을_거부한다() -> None:
    with pytest.raises(RuntimeError, match="응답 형식"):
        parse_observations({"observations": "invalid"})
