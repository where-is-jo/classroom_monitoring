from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Self

import pytest

from deeplearning.training.verify_face_cutover import verify_cutover_endpoint


class Response:
    def __init__(self, value: dict[str, Any]) -> None:
        self._body = json.dumps(value).encode()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        del args

    def read(self) -> bytes:
        return self._body


class Opener:
    def __init__(self, *, ready_model: str = "adaface") -> None:
        self.ready_model = ready_model
        self.requests: list[object] = []

    def __call__(self, request: object, *, timeout: float) -> Response:
        assert timeout == 5.0
        self.requests.append(request)
        if len(self.requests) == 1:
            return Response(
                {
                    "status": "ready",
                    "face_identification": "ready",
                    "active_face_model": self.ready_model,
                    "missing_gallery_entries": "0",
                }
            )
        return Response({"observations": [{"identity_status": "UNKNOWN"}]})


def _image(tmp_path: Path) -> Path:
    path = tmp_path / "probe.jpg"
    path.write_bytes(b"jpeg")
    return path


def test_활성_AdaFace_readiness와_5초_요청을_검증한다(tmp_path: Path) -> None:
    opener = Opener()
    times = iter((10.0, 12.5))

    result = verify_cutover_endpoint(
        base_url="http://deeplearning:8100",
        image_path=_image(tmp_path),
        expected_model="adaface",
        opener=opener,
        clock=lambda: next(times),
    )

    assert result.model_name == "adaface"
    assert result.elapsed_seconds == 2.5
    assert result.observation_count == 1
    assert len(opener.requests) == 2


def test_readiness의_활성_모델이_다르면_요청하지_않는다(tmp_path: Path) -> None:
    opener = Opener(ready_model="arcface")

    with pytest.raises(RuntimeError, match="readiness"):
        verify_cutover_endpoint(
            base_url="http://deeplearning:8100",
            image_path=_image(tmp_path),
            expected_model="adaface",
            opener=opener,
        )

    assert len(opener.requests) == 1


def test_실제_식별_요청이_5초를_넘으면_실패한다(tmp_path: Path) -> None:
    opener = Opener()
    times = iter((10.0, 15.001))

    with pytest.raises(RuntimeError, match="5.000초"):
        verify_cutover_endpoint(
            base_url="http://deeplearning:8100",
            image_path=_image(tmp_path),
            expected_model="adaface",
            opener=opener,
            clock=lambda: next(times),
        )


def test_빈_검증_이미지는_서버_호출_전에_거부한다(tmp_path: Path) -> None:
    image = tmp_path / "empty.jpg"
    image.write_bytes(b"")
    opener = Opener()

    with pytest.raises(ValueError, match="비어"):
        verify_cutover_endpoint(
            base_url="http://deeplearning:8100",
            image_path=image,
            expected_model="adaface",
            opener=opener,
        )

    assert opener.requests == []
