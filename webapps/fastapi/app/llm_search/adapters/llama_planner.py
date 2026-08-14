"""llama.cpp 서버 HTTP 어댑터.

`.docker/compose.llm.yml`이 띄우는 llama-server가 OpenAI 호환 API를 연다.
여기서는 **HTTP만** 한다. 응답을 계획으로 해석하는 일은 `planning.py`가 한다.

## 요청에 붙이는 값의 이유

- `temperature: 0` — 같은 질문이 매번 다른 계획을 내면 기능 자체를 시험할 수 없다
- `max_tokens` — 타임아웃은 멈추지 않는 모델의 해법이 아니다. 타임아웃 안에서
  수 MB를 읽을 수 있다. 생성 길이 자체를 막는다
- `response_format: json_object` — 설명을 덧붙이는 실패를 줄인다. 다만 이것만으로
  키가 맞는다는 보장은 없어서 검증은 그대로 한다

## GPU를 추론 워커와 나눠 쓴다

llama-server와 inference-worker가 같은 GPU를 쓴다(compose 주석). 인증이 없는
상태(결정 0010)라 검색이 몰리면 탐지 파이프라인이 느려질 수 있다. 질문 길이 상한
(200자)과 `max_tokens`가 지금 있는 유일한 제동 장치다.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from ..errors import LlmSearchPlanInvalidError, LlmSearchPlannerUnavailableError
from ..ports import PlanPrompt

# 계획 JSON은 200바이트 남짓이다. 넉넉히 잡아도 이 정도면 끝난다.
_MAX_TOKENS = 256

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
            response = self._post(
                self._url,
                json={
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": prompt.system},
                        {"role": "user", "content": prompt.question},
                    ],
                    "temperature": 0,
                    "max_tokens": _MAX_TOKENS,
                    "response_format": {"type": "json_object"},
                },
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as error:
            raise LlmSearchPlannerUnavailableError() from error
        except ValueError as error:
            # 본문이 JSON이 아니다. 서버는 살아 있으므로 연결 문제와 구분한다.
            raise LlmSearchPlanInvalidError("RESPONSE_NOT_JSON") from error

        return _extract_content(payload)


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
