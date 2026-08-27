"""입구 얼굴 오버레이 전송 경로의 계약.

확인하는 것은 셋이다 — 저장과 다른 endpoint로 가는가, 본문이 저장 경로와 같은가,
실패해도 파이프라인을 흔들지 않는가.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import numpy as np
import requests
from shared.types import CapturedFrame

from inference.entry_overlay import ENTRY_OVERLAY_PATH, FastAPIEntryOverlayHandler
from inference.face_identity import (
    ENTRY_IDENTITY_EVENTS_PATH,
    build_entry_identity_event_payload,
)
from inference.types import (
    EntryFaceObservation,
    EntryFaceObservationBatch,
    EntryIdentityProcessingStatus,
    EntryIdentityStatus,
)


class _Response:
    def __init__(self, status_code: int = 202) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")


def _captured() -> CapturedFrame:
    return CapturedFrame(
        camera_id="camera-01",
        frame=np.zeros((8, 8, 3), dtype=np.uint8),
        captured_at=datetime(2026, 8, 27, 4, 0, tzinfo=UTC),
        sequence=7,
    )


def _batch() -> EntryFaceObservationBatch:
    return EntryFaceObservationBatch(
        frame_shape=(8, 8, 3),
        processing_status=EntryIdentityProcessingStatus.SUCCEEDED,
        observations=(
            EntryFaceObservation(
                face_track_id="track-1",
                face_bbox=(1, 1, 5, 5),
                detection_confidence=0.9,
                identity_status=EntryIdentityStatus.UNKNOWN,
                student_id=None,
                similarity=None,
                margin=None,
                quality=0.8,
                observation_count=3,
                rejected_reason=None,
            ),
        ),
    )


def test_저장이_아닌_오버레이_endpoint로_보낸다() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_post(url: str, **kwargs: Any) -> _Response:
        calls.append((url, kwargs))
        return _Response()

    FastAPIEntryOverlayHandler("http://fastapi:8001/", post=fake_post)(
        _captured(), _batch()
    )

    url, kwargs = calls[0]
    assert url == "http://fastapi:8001" + ENTRY_OVERLAY_PATH
    # 저장 경로와 반드시 달라야 한다.
    assert ENTRY_OVERLAY_PATH != ENTRY_IDENTITY_EVENTS_PATH
    assert kwargs["timeout"] == 2.0


def test_본문은_저장_경로와_같은_모양이다() -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(_url: str, **kwargs: Any) -> _Response:
        calls.append(kwargs)
        return _Response()

    captured, batch = _captured(), _batch()
    FastAPIEntryOverlayHandler("http://fastapi:8001", post=fake_post)(captured, batch)

    # 두 경로가 다른 모양을 보내면 화면의 상자와 저장된 관측이 어긋날 수 있다.
    assert calls[0]["json"] == build_entry_identity_event_payload(captured, batch)


def test_전송이_실패해도_예외가_새지_않는다() -> None:
    def failing_post(_url: str, **_kwargs: Any) -> _Response:
        raise requests.ConnectionError("fastapi가 죽어 있다")

    # 다음 프레임이 곧 같은 자리를 덮어 그리므로 오버레이 실패는 삼킨다.
    FastAPIEntryOverlayHandler("http://fastapi:8001", post=failing_post)(
        _captured(), _batch()
    )


def test_오류_응답도_삼킨다() -> None:
    def error_post(_url: str, **_kwargs: Any) -> _Response:
        return _Response(status_code=500)

    FastAPIEntryOverlayHandler("http://fastapi:8001", post=error_post)(
        _captured(), _batch()
    )
