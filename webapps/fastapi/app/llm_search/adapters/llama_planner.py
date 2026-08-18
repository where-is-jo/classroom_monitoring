"""llama.cpp 서버 HTTP 어댑터.

`.docker/compose.llm.yml`이 띄우는 llama-server가 OpenAI 호환 API를 연다.
여기서는 **HTTP만** 한다. 응답을 계획으로 해석하는 일은 `planning.py`가 한다.

## 요청에 붙이는 값의 이유

- `temperature: 0` — 같은 질문이 매번 다른 계획을 내면 기능 자체를 시험할 수 없다
- `max_tokens` — 타임아웃은 멈추지 않는 모델의 해법이 아니다. 타임아웃 안에서
  수 MB를 읽을 수 있다. 생성 길이 자체를 막는다
- `response_format: json_schema` — llama.cpp가 스키마를 grammar로 바꿔 **생성
  단계에서** 구조를 강제한다. 코드펜스·설명·없는 키·틀린 타입이 애초에 나오지
  않는다. 저양자화 모델에서 특히 크게 작동한다. 다만 이것으로 값이 맞는다는 보장은
  없어서 검증은 그대로 한다

## 스키마를 지원하지 않는 서버로 폴백한다

`json_schema`를 모르는 빌드는 요청 자체를 4xx로 거절한다. 그러면 기능이 통째로
죽는데, **우리는 아직 실제 서버에서 확인하지 못했다.** 4xx를 받으면 한 번만
`json_object`로 낮춰 다시 보낸다. 재시도가 아니라 호환성 처리라 이 계층에 둔다 —
모델이 규격을 벗어난 경우의 재시도는 서비스가 담당한다.

## GPU를 추론 워커와 나눠 쓴다

llama-server와 inference-worker가 같은 GPU를 쓴다(compose 주석). 인증이 없는
상태(결정 0010)라 검색이 몰리면 탐지 파이프라인이 느려질 수 있다. 질문 길이 상한
(200자)과 `max_tokens`가 지금 있는 유일한 제동 장치다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import httpx

from ..errors import LlmSearchPlanInvalidError, LlmSearchPlannerUnavailableError
from ..planning import PLAN_JSON_SCHEMA
from ..ports import PlanPrompt

logger = logging.getLogger(__name__)

# 계획 JSON은 200바이트 남짓이다. 넉넉히 잡아도 이 정도면 끝난다.
_MAX_TOKENS = 256

# 오류 본문 전체를 로그에 붓지 않는다. 원인을 가리는 문장은 앞쪽에 있다.
_MAX_ERROR_LOG_CHARS = 300

_SCHEMA_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {"name": "detection_search_plan", "schema": PLAN_JSON_SCHEMA},
}
_OBJECT_FORMAT: dict[str, Any] = {"type": "json_object"}

PostCallable = Callable[..., httpx.Response]


class LlamaQueryPlanner:
    """llama-server의 chat completions로 계획을 받는다."""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        model: str,
        *,
        post: PostCallable = httpx.post,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/v1/chat/completions"
        self._timeout = timeout_seconds
        self._model = model
        # 호출자를 주입받아 HTTP 없이 시험한다. 주입 지점을 두지 않으면 이 어댑터의
        # 응답 해석에 테스트를 붙일 방법이 없다.
        self._post = post

    def plan(self, prompt: PlanPrompt) -> str:
        try:
            response = self._request(prompt, _SCHEMA_FORMAT)
            if 400 <= response.status_code < 500:
                # 스키마를 모르는 빌드다. 5xx는 폴백해도 같은 결과라 그대로 둔다.
                #
                # **본문을 함께 남긴다.** 스키마 미지원·모델명 불일치·컨텍스트 초과·
                # 템플릿이 거부한 role이 전부 4xx라, 상태 코드만 남기면 어느 쪽인지
                # 알 수 없다. 폴백까지 실패해 503이 나가면 남는 단서가 이 줄뿐이다.
                # 응답에는 실리지 않는다 — 이것은 서버가 낸 오류 메시지이지 모델
                # 생성물이 아니라, 결정 0016이 막는 "모델 원문"에 해당하지 않는다.
                logger.warning(
                    "llama-server가 json_schema 요청을 거절했다(%s): %s. json_object로 낮춘다",
                    response.status_code,
                    _brief(response),
                )
                response = self._request(prompt, _OBJECT_FORMAT)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as error:
            raise LlmSearchPlannerUnavailableError() from error
        except ValueError as error:
            # 본문이 JSON이 아니다. 서버는 살아 있으므로 연결 문제와 구분한다.
            raise LlmSearchPlanInvalidError("RESPONSE_NOT_JSON") from error

        return _extract_content(payload)

    def _request(self, prompt: PlanPrompt, response_format: dict[str, Any]) -> httpx.Response:
        return self._post(
            self._url,
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": prompt.system},
                    {"role": "user", "content": prompt.question},
                ],
                "temperature": 0,
                "max_tokens": _MAX_TOKENS,
                "response_format": response_format,
            },
            timeout=self._timeout,
        )


def _brief(response: httpx.Response) -> str:
    """오류 본문의 앞부분만 꺼낸다.

    서버가 무엇을 돌려주든 로깅이 실패해서는 안 된다. 본문이 비어 있거나 디코딩할 수
    없으면 빈 문자열로 둔다 — 진단을 돕자고 넣은 코드가 새 실패 경로가 되면 안 된다.
    """
    try:
        return response.text[:_MAX_ERROR_LOG_CHARS]
    except (UnicodeDecodeError, httpx.HTTPError):
        return "<본문을 읽지 못함>"


def _extract_content(payload: object) -> str:
    """`choices[0].message.content`를 방어적으로 꺼낸다.

    구조를 그대로 믿고 인덱싱하면 서버가 형식을 바꾸거나 빈 응답을 낼 때 500이
    난다. 모델이 규격을 벗어난 것과 같은 종류의 실패이므로 같은 오류로 다룬다.
    """
    if not isinstance(payload, dict):
        raise LlmSearchPlanInvalidError("RESPONSE_MALFORMED")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LlmSearchPlanInvalidError("RESPONSE_MALFORMED")
    first: Any = choices[0]
    if not isinstance(first, dict):
        raise LlmSearchPlanInvalidError("RESPONSE_MALFORMED")
    message = first.get("message")
    if not isinstance(message, dict):
        raise LlmSearchPlanInvalidError("RESPONSE_MALFORMED")
    content = message.get("content")
    if not isinstance(content, str):
        raise LlmSearchPlanInvalidError("RESPONSE_MALFORMED")
    return content
