"""오버레이 전용 전송 경로의 계약.

확인하는 것은 셋이다 — 저장 경로와 다른 endpoint로 가는가, 본문이 저장 경로와
같은가, 실패해도 파이프라인을 흔들지 않는가.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import numpy as np
import requests
from shared.types import CapturedFrame

from inference.handler import build_event_payload
from inference.overlay import OVERLAY_PATH, FastAPIOverlayHandler
from inference.types import Detection, InferenceResult


class _Response:
    def __init__(self, status_code: int = 202) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")


def _captured() -> CapturedFrame:
    return CapturedFrame(
        camera_id="classroom-cctv",
        frame=np.zeros((4, 4, 3), dtype=np.uint8),
        captured_at=datetime(2026, 8, 26, 9, 0, tzinfo=UTC),
        sequence=42,
    )


def _result() -> InferenceResult:
    return InferenceResult(
        frame_shape=(4, 4, 3),
        detections=(
            Detection(class_id=0, class_name="person", confidence=0.9, bbox=(1, 1, 3, 3)),
        ),
    )


def test_저장이_아닌_오버레이_endpoint로_보낸다() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_post(url: str, **kwargs: Any) -> _Response:
        calls.append((url, kwargs))
        return _Response()

    handler = FastAPIOverlayHandler("http://fastapi:8001/", post=fake_post)
    handler(_captured(), _result())

    assert len(calls) == 1
    url, kwargs = calls[0]
    # 저장 경로(/internal/inference/events)와 다른 자리로 가야 한다.
    assert url == "http://fastapi:8001" + OVERLAY_PATH
    assert OVERLAY_PATH == "/internal/inference/overlays"
    assert kwargs["timeout"] == 2.0


def test_본문은_저장_경로와_같은_모양이다() -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(_url: str, **kwargs: Any) -> _Response:
        calls.append(kwargs)
        return _Response()

    captured, result = _captured(), _result()
    FastAPIOverlayHandler("http://fastapi:8001", post=fake_post)(captured, result)

    # 두 경로가 다른 모양을 보내면 화면의 상자와 저장된 상자가 어긋날 수 있다.
    assert calls[0]["json"] == build_event_payload(captured, result)


def test_전송이_실패해도_예외가_새지_않는다() -> None:
    def failing_post(_url: str, **_kwargs: Any) -> _Response:
        raise requests.ConnectionError("fastapi가 죽어 있다")

    handler = FastAPIOverlayHandler("http://fastapi:8001", post=failing_post)

    # 다음 프레임이 곧 같은 자리를 덮어 그리므로 오버레이 실패는 삼킨다.
    handler(_captured(), _result())


def test_오류_응답도_삼킨다() -> None:
    def error_post(_url: str, **_kwargs: Any) -> _Response:
        return _Response(status_code=500)

    FastAPIOverlayHandler("http://fastapi:8001", post=error_post)(_captured(), _result())
