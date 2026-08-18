"""llama.cpp 어댑터의 계약.

**실제 서버를 부르지 않는다.** 호출자를 주입해 요청 본문과 응답 해석을 고정한다.
GPU 서버 없이 확인할 수 있는 것은 여기까지이며, 모델이 실제로 규격에 맞는 JSON을
얼마나 안정적으로 내는지는 별개 문제다.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app.llm_search.adapters.llama_planner import LlamaQueryPlanner
from app.llm_search.errors import LlmSearchPlanInvalidError, LlmSearchPlannerUnavailableError
from app.llm_search.planning import PLAN_JSON_SCHEMA
from app.llm_search.ports import PlanPrompt

_PROMPT = PlanPrompt(
    system="지시문",
    question="오늘 A101에 누가 있었어?",
    now=datetime(2026, 8, 14, 10, 0, tzinfo=UTC),
)


def _response(payload: object, *, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        content=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        request=httpx.Request("POST", "http://llama/v1/chat/completions"),
    )


def _planner(response: httpx.Response | Exception) -> tuple[LlamaQueryPlanner, dict[str, Any]]:
    captured: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        captured["url"] = url
        captured.update(kwargs)
        if isinstance(response, Exception):
            raise response
        return response

    return LlamaQueryPlanner("http://llama:8008/", 20.0, "gemma", post=fake_post), captured


def _sequence_planner(*responses: httpx.Response) -> tuple[LlamaQueryPlanner, list[dict[str, Any]]]:
    """호출마다 다른 응답을 돌려준다. 폴백을 확인하려면 두 번의 요청을 봐야 한다."""
    sent: list[dict[str, Any]] = []
    remaining = list(responses)

    def fake_post(url: str, **kwargs: Any) -> httpx.Response:
        sent.append(kwargs["json"])
        return remaining.pop(0)

    return LlamaQueryPlanner("http://llama:8008/", 20.0, "gemma", post=fake_post), sent


def test_모델_원문을_그대로_돌려준다() -> None:
    """해석은 어댑터가 하지 않는다. 검증 없이 통과시키면 방어선이 두 곳이 된다."""
    content = '{"intent":"detection_search"}'
    planner, _ = _planner(_response({"choices": [{"message": {"content": content}}]}))

    assert planner.plan(_PROMPT) == content


def test_결정적인_응답을_받도록_요청한다() -> None:
    """같은 질문이 매번 다른 계획을 내면 기능을 시험할 수 없다."""
    planner, captured = _planner(_response({"choices": [{"message": {"content": "{}"}}]}))

    planner.plan(_PROMPT)

    body = captured["json"]
    assert body["temperature"] == 0
    assert body["model"] == "gemma"
    # 타임아웃은 멈추지 않는 모델의 해법이 아니다. 생성 길이 자체를 막는다.
    assert body["max_tokens"] > 0
    assert captured["timeout"] == 20.0
    assert captured["url"] == "http://llama:8008/v1/chat/completions"


def test_생성_단계에서_스키마로_구조를_강제한다() -> None:
    """저양자화 모델에서 코드펜스·없는 키가 애초에 나오지 않게 만드는 장치다."""
    planner, captured = _planner(_response({"choices": [{"message": {"content": "{}"}}]}))

    planner.plan(_PROMPT)

    response_format = captured["json"]["response_format"]
    assert response_format["type"] == "json_schema"
    schema = response_format["json_schema"]["schema"]
    # 검증의 정본은 planning.py다. 어댑터가 규격을 따로 들고 있으면 갈라진다.
    assert schema is PLAN_JSON_SCHEMA
    assert schema["additionalProperties"] is False


def test_스키마를_모르는_서버에는_json_object로_낮춰_다시_보낸다() -> None:
    """지원 여부를 아직 실측하지 못했다. 거절당하면 기능이 통째로 죽는다."""
    planner, sent = _sequence_planner(
        _response({"error": "unsupported response_format"}, status=400),
        _response({"choices": [{"message": {"content": '{"intent":"detection_search"}'}}]}),
    )

    assert planner.plan(_PROMPT) == '{"intent":"detection_search"}'
    assert [body["response_format"]["type"] for body in sent] == ["json_schema", "json_object"]


def test_서버_오류에는_폴백하지_않는다() -> None:
    """5xx는 낮춰 보내도 같은 결과다. 요청만 두 배가 된다."""
    planner, sent = _sequence_planner(_response({"error": "no model"}, status=503))

    with pytest.raises(LlmSearchPlannerUnavailableError):
        planner.plan(_PROMPT)

    assert len(sent) == 1


def test_지시문과_질문을_나눠_보낸다() -> None:
    planner, captured = _planner(_response({"choices": [{"message": {"content": "{}"}}]}))

    planner.plan(_PROMPT)

    messages = captured["json"]["messages"]
    assert messages[0] == {"role": "system", "content": "지시문"}
    assert messages[1] == {"role": "user", "content": "오늘 A101에 누가 있었어?"}


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectError("연결 실패"),
        httpx.ReadTimeout("응답 없음"),
    ],
)
def test_서버에_닿지_못하면_사용_불가로_바꾼다(failure: Exception) -> None:
    planner, _ = _planner(failure)

    with pytest.raises(LlmSearchPlannerUnavailableError):
        planner.plan(_PROMPT)


def test_오류_상태_코드도_사용_불가다() -> None:
    planner, _ = _planner(_response({"error": "no model"}, status=503))

    with pytest.raises(LlmSearchPlannerUnavailableError):
        planner.plan(_PROMPT)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": []},
        {"choices": [{}]},
        {"choices": ["문자열"]},
        {"choices": [{"message": {}}]},
        {"choices": [{"message": {"content": 42}}]},
        ["배열"],
    ],
)
def test_응답_구조가_예상과_다르면_500이_아니라_계획_오류다(payload: object) -> None:
    """구조를 믿고 인덱싱하면 서버 형식이 바뀔 때 500이 난다. 서버는 살아 있다."""
    planner, _ = _planner(_response(payload))

    with pytest.raises(LlmSearchPlanInvalidError):
        planner.plan(_PROMPT)


def test_본문이_JSON이_아니면_연결_문제와_구분한다() -> None:
    response = httpx.Response(
        status_code=200,
        content=b"<html>proxy error</html>",
        request=httpx.Request("POST", "http://llama/v1/chat/completions"),
    )
    planner, _ = _planner(response)

    with pytest.raises(LlmSearchPlanInvalidError):
        planner.plan(_PROMPT)


def test_거절당한_이유를_로그에_남긴다(caplog: pytest.LogCaptureFixture) -> None:
    """상태 코드만으로는 원인을 가릴 수 없다.

    스키마 미지원·모델명 불일치·컨텍스트 초과·템플릿이 거부한 role이 전부 4xx다.
    폴백까지 실패하면 사용자에게는 503만 나가므로, 서버 로그의 이 한 줄이 유일한
    단서가 된다. 본문이 없으면 "json_object로 낮췄다"는 사실만 남아 진단이 막힌다.
    """
    planner, _ = _sequence_planner(
        _response({"error": {"message": "System role not supported"}}, status=400),
        _response({"choices": [{"message": {"content": '{"intent":"detection_search"}'}}]}),
    )

    with caplog.at_level(logging.WARNING):
        planner.plan(_PROMPT)

    assert "System role not supported" in caplog.text


def test_오류_본문이_길어도_로그를_뒤덮지_않는다(caplog: pytest.LogCaptureFixture) -> None:
    """서버가 프롬프트를 통째로 되돌려주는 경우가 있다. 앞부분이면 원인은 충분히 읽힌다."""
    planner, _ = _sequence_planner(
        _response({"error": "가" * 5000}, status=400),
        _response({"choices": [{"message": {"content": '{"intent":"detection_search"}'}}]}),
    )

    with caplog.at_level(logging.WARNING):
        planner.plan(_PROMPT)

    assert len(caplog.text) < 2000
