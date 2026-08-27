"""탐지 결과를 화면 오버레이 전용 경로로 보낸다.

## 왜 저장 경로와 나누는가

브라우저의 영상은 mediamtx에서 WebRTC로 직통하지만 bbox는 이 워커 → FastAPI →
SSE를 거친다. 그런데 그 FastAPI 경로가 탐지 이벤트 저장과 좌석 판정까지 함께 하는
경로라, 저장 주기가 곧 화면 갱신 주기가 됐다. 실측(2026-08-26)에서 SSE 갱신 간격이
p50 3.27초, p99 18.4초였다. 영상은 초당 20프레임으로 흐르는데 상자만 3초에 한 번
움직이니 사람 뒤를 끌려다니는 것처럼 보인다.

두 경로는 요구가 반대다.

| | 필요한 것 |
| --- | --- |
| 오버레이 | 자주(추론 주기 그대로), 저장 불필요, 유실 무방 |
| 탐지 이벤트 저장 | 가끔이어도 됨, 반드시 저장, 멱등, 좌석 판정까지 |

그래서 오버레이는 저장을 하지 않는 `/internal/inference/overlays`로 따로 보낸다.
그 endpoint는 저장소를 건드리지 않아 실측 12ms대로 끝난다.

## 실패를 재시도하지 않는다

`FastAPIResultHandler`는 전송 실패를 재시도한다. 그 이벤트를 놓치면 다시 만들 수
없기 때문이다. 오버레이는 반대다 — 다음 프레임이 곧 같은 자리를 덮어 그리므로,
재시도하며 붙잡고 있는 것이 더 나쁘다. 한 번 보내고 실패하면 버린다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import requests
from shared.types import CapturedFrame

from .handler import build_event_payload
from .types import InferenceResult

logger = logging.getLogger(__name__)

OVERLAY_PATH = "/internal/inference/overlays"
# 저장을 하지 않는 endpoint라 정상 응답은 수십 ms다. 이보다 오래 걸리면 이미 다음
# 프레임이 나왔으므로 기다릴 이유가 없다.
POST_TIMEOUT_SECONDS = 2.0

__all__ = ["FastAPIOverlayHandler", "OVERLAY_PATH"]


class FastAPIOverlayHandler:
    """탐지 결과를 오버레이 endpoint로 보낸다. `ResultHandler` 자리에 그대로 들어간다.

    본문은 저장 경로와 같은 `build_event_payload`를 쓴다. 두 경로가 서로 다른 모양을
    보내면 브라우저가 그리는 상자와 저장된 상자가 어긋날 수 있다.
    """

    def __init__(
        self,
        fastapi_url: str,
        *,
        timeout_seconds: float = POST_TIMEOUT_SECONDS,
        post: Callable[..., requests.Response] = requests.post,
    ) -> None:
        self._url = fastapi_url.rstrip("/") + OVERLAY_PATH
        self._timeout_seconds = timeout_seconds
        self._post = post

    def __call__(self, captured: CapturedFrame, result: InferenceResult) -> None:
        payload = build_event_payload(captured, result)
        try:
            response = self._post(self._url, json=payload, timeout=self._timeout_seconds)
            response.raise_for_status()
        except Exception:
            # **오버레이 실패로 파이프라인을 흔들지 않는다.** 다음 프레임이 곧 같은
            # 자리를 덮어 그린다. debug로 남기는 이유는 초당 여러 번 도는 자리라
            # 끊긴 동안 로그가 그것만으로 가득 차기 때문이다.
            logger.debug(
                "오버레이 전송 실패 (카메라 %s 프레임 %d)",
                captured.camera_id,
                captured.sequence,
                exc_info=True,
            )
