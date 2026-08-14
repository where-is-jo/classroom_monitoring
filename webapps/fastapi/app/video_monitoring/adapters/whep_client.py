"""MediaMTX WHEP signaling HTTP 어댑터 (결정 0014).

URL은 호출자가 서버 쪽에서만 조립한다. 이 어댑터는 받은 URL 그대로 호출하며,
SDP 이외의 클라이언트 값은 proxy target에 절대 반영하지 않는다(SSRF 차단).
MediaMTX 응답 본문·상태는 로그로만 남기고 브라우저에 그대로 전달하지 않는다.
"""

from __future__ import annotations

import logging

import httpx

from ..errors import WhepTimeoutError, WhepUnavailableError
from ..ports import WhepPostResult

logger = logging.getLogger(__name__)


class HttpWhepClient:
    """httpx 기반 WHEP signaling 클라이언트."""

    def __init__(self, *, timeout_seconds: float) -> None:
        self._timeout_seconds = timeout_seconds

    def post_offer(self, target_url: str, sdp: str) -> WhepPostResult:
        """새 WHEP offer를 보내 answer SDP와 resource location을 받는다.

        resource location은 응답 Location 헤더의 원문이다. 서비스가 같은 origin
        검증을 마친 뒤에만 보관·재사용한다(결정 0014의 SSRF 차단).
        """
        try:
            response = httpx.post(
                target_url,
                content=sdp,
                headers={
                    "Content-Type": "application/sdp",
                    "Accept": "application/sdp",
                },
                timeout=self._timeout_seconds,
            )
        except httpx.TimeoutException:
            logger.warning("WHEP POST timeout: %s", target_url)
            raise WhepTimeoutError() from None
        except httpx.HTTPError:
            logger.warning("WHEP POST unavailable: %s", target_url, exc_info=True)
            raise WhepUnavailableError() from None
        if response.is_error:
            logger.warning("WHEP POST failed: status=%s url=%s", response.status_code, target_url)
            raise WhepUnavailableError()
        location = response.headers.get("location") or ""
        return WhepPostResult(answer_sdp=response.text, resource_location=location)

    def patch_offer(self, resource_url: str, sdp: str) -> str:
        """재협상 offer를 보내 answer SDP를 받는다."""
        try:
            response = httpx.patch(
                resource_url,
                content=sdp,
                headers={
                    "Content-Type": "application/sdp",
                    "Accept": "application/sdp",
                },
                timeout=self._timeout_seconds,
            )
        except httpx.TimeoutException:
            logger.warning("WHEP PATCH timeout: %s", resource_url)
            raise WhepTimeoutError() from None
        except httpx.HTTPError:
            logger.warning("WHEP PATCH unavailable: %s", resource_url, exc_info=True)
            raise WhepUnavailableError() from None
        if response.is_error:
            logger.warning(
                "WHEP PATCH failed: status=%s url=%s", response.status_code, resource_url
            )
            raise WhepUnavailableError()
        return response.text

    def delete(self, resource_url: str) -> None:
        """WHEP resource를 닫는다. 2xx이면 성공으로 본다."""
        try:
            response = httpx.delete(resource_url, timeout=self._timeout_seconds)
        except httpx.TimeoutException:
            logger.warning("WHEP DELETE timeout: %s", resource_url)
            raise WhepTimeoutError() from None
        except httpx.HTTPError:
            logger.warning("WHEP DELETE unavailable: %s", resource_url, exc_info=True)
            raise WhepUnavailableError() from None
        if response.is_error:
            logger.warning(
                "WHEP DELETE failed: status=%s url=%s", response.status_code, resource_url
            )
            raise WhepUnavailableError()
