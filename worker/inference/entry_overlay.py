"""입구 얼굴 관측을 화면 오버레이 전용 경로로 보낸다.

CCTV 탐지에서 같은 문제를 겪고 갈랐던 것을 입구에도 적용한다
(`inference/overlay.py`, 결정 0047).

브라우저의 입구 영상은 mediamtx에서 WebRTC로 직통하지만 얼굴 상자는 이 워커 →
FastAPI → SSE를 거친다. 그런데 그 FastAPI 경로가 관측 저장까지 함께 하는 경로라
저장 주기가 곧 화면 갱신 주기가 됐다.

두 경로는 요구가 반대다.

| | 필요한 것 |
| --- | --- |
| 얼굴 상자 오버레이 | 자주(추론 주기 그대로), 저장 불필요, 유실 무방 |
| 입구 관측 저장 | 가끔이어도 됨, 반드시 저장, 멱등, 만료 정책까지 |

## 실패를 재시도하지 않는다

`FastAPIEntryIdentityEventHandler`는 전송 실패를 재시도한다. 그 관측을 놓치면 다시
만들 수 없기 때문이다. 오버레이는 반대다 — 다음 프레임이 곧 같은 자리를 덮어
그리므로, 재시도하며 붙잡고 있는 것이 더 나쁘다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import requests
from shared.types import CapturedFrame

from .face_identity import build_entry_identity_event_payload
from .types import EntryFaceObservationBatch

logger = logging.getLogger(__name__)

ENTRY_OVERLAY_PATH = "/internal/entry-identity-overlays"
# 저장을 하지 않는 endpoint라 정상 응답은 수십 ms다. 이보다 오래 걸리면 이미 다음
# 프레임이 나왔으므로 기다릴 이유가 없다.
POST_TIMEOUT_SECONDS = 2.0

__all__ = ["ENTRY_OVERLAY_PATH", "FastAPIEntryOverlayHandler"]


class FastAPIEntryOverlayHandler:
    """얼굴 관측을 오버레이 endpoint로 보낸다. `EntryResultHandler` 자리에 들어간다.

    본문은 저장 경로와 같은 `build_entry_identity_event_payload`를 쓴다. 두 경로가
    서로 다른 모양을 보내면 화면의 상자와 저장된 관측이 어긋날 수 있다.
    """

    def __init__(
        self,
        fastapi_url: str,
        *,
        timeout_seconds: float = POST_TIMEOUT_SECONDS,
        post: Callable[..., requests.Response] = requests.post,
    ) -> None:
        self._url = fastapi_url.rstrip("/") + ENTRY_OVERLAY_PATH
        self._timeout_seconds = timeout_seconds
        self._post = post

    def __call__(
        self,
        captured: CapturedFrame,
        batch: EntryFaceObservationBatch,
    ) -> None:
        payload = build_entry_identity_event_payload(captured, batch)
        try:
            response = self._post(self._url, json=payload, timeout=self._timeout_seconds)
            response.raise_for_status()
        except Exception:
            # **오버레이 실패로 파이프라인을 흔들지 않는다.** 다음 프레임이 곧 같은
            # 자리를 덮어 그린다. debug로 남기는 이유는 초당 여러 번 도는 자리라
            # 끊긴 동안 로그가 그것만으로 가득 차기 때문이다.
            logger.debug(
                "입구 오버레이 전송 실패 (카메라 %s 프레임 %d)",
                captured.camera_id,
                captured.sequence,
                exc_info=True,
            )
