"""프롬프트와 검증 규칙은 한 쌍이다.

한쪽만 바뀌면 모델이 규격에 맞는 답을 내도 422가 되거나, 규격을 벗어난 답이
통과한다. 계약 문구가 프롬프트에 실제로 들어가는지 여기서 고정한다.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.llm_search.models import CameraChoice
from app.llm_search.planning import MAX_LIMIT
from app.llm_search.prompts import build_system_prompt

_NOW = datetime(2026, 8, 14, 0, 30, tzinfo=UTC)  # KST로는 같은 날 09:30


def _prompt(cameras: list[CameraChoice]) -> str:
    return build_system_prompt(now=_NOW, cameras=cameras, max_limit=MAX_LIMIT)


def test_현재_시각을_KST와_UTC로_함께_알려준다() -> None:
    """사용자는 KST로 말하고 저장소는 UTC로 조회한다. 둘 다 줘야 변환이 가능하다."""
    prompt = _prompt([])

    assert "2026-08-14 09:30:00" in prompt
    assert "2026-08-14T00:30:00Z" in prompt


def test_등록된_카메라_식별자를_그대로_알려준다() -> None:
    prompt = _prompt(
        [
            CameraChoice(camera_id="camera-01", classroom_id="A101", label="A101 앞문"),
            CameraChoice(camera_id="camera-02", classroom_id="B203", label="B203 뒷문"),
        ]
    )

    assert "camera_id=camera-01" in prompt
    assert "classroom_id=B203" in prompt
    assert "지어내지 마라" in prompt


def test_카메라가_없으면_null만_쓰라고_알린다() -> None:
    """빈 목록을 그대로 보여주면 모델이 빈칸을 채우려 든다."""
    prompt = _prompt([])

    assert "등록된 카메라가 없다" in prompt


def test_계약의_핵심_규칙이_프롬프트에_들어_있다() -> None:
    prompt = _prompt([])

    assert "detection_search" in prompt
    assert f"{MAX_LIMIT} 이하" in prompt
    assert "Z로 끝난다" in prompt
    assert "다른 키를 넣지 마라" in prompt
