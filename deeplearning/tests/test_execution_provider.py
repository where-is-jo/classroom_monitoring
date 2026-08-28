"""얼굴 모델의 실행 provider 선택 계약.

**이 파일이 지키는 것은 "조용히 CPU로 내려가지 않는다"이다.** onnxruntime은 의존
라이브러리가 없어 CUDA provider 로드에 실패해도 `get_available_providers()` 목록에는
그대로 두고 세션만 CPU로 만든다. 실제로 그 상태로 오래 돌았고, 지표에는 "얼굴 분석이
느리다"로만 보였다. 그래서 목록이 아니라 **세션이 실제로 쓰는 provider**를 본다.
"""

from __future__ import annotations

import logging

import pytest

from deeplearning.execution_provider import (
    active_provider,
    requested_providers,
    verify_execution_provider,
)


class _Session:
    def __init__(self, provider: str) -> None:
        self._provider = provider

    def get_providers(self) -> list[str]:
        return [self._provider, "CPUExecutionProvider"]


class _Model:
    """insightface 모델 중 provider 확인에 쓰는 부분만 흉내낸다."""

    def __init__(self, provider: str) -> None:
        self.session = _Session(provider)


class _ModelWithoutSession:
    """session을 노출하지 않는 모델. 확인할 수 없는 경우다."""


def test_기본값은_auto이고_CUDA를_먼저_요청한다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FACE_EXECUTION_PROVIDER", raising=False)

    choice, providers = requested_providers()

    assert choice == "auto"
    # CPU를 뒤에 두어야 CUDA가 안 될 때 기동 자체는 된다.
    assert providers == ["CUDAExecutionProvider", "CPUExecutionProvider"]


def test_cpu로_지정하면_CUDA를_요청하지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FACE_EXECUTION_PROVIDER", "cpu")

    choice, providers = requested_providers()

    assert choice == "cpu"
    assert providers == ["CPUExecutionProvider"]


def test_알_수_없는_값은_기동을_막는다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FACE_EXECUTION_PROVIDER", "gpu")

    with pytest.raises(RuntimeError, match="auto, cuda, cpu"):
        requested_providers()


def test_실제_provider는_목록이_아니라_세션에서_읽는다() -> None:
    assert active_provider(_Model("CUDAExecutionProvider")) == "CUDAExecutionProvider"
    assert active_provider(_Model("CPUExecutionProvider")) == "CPUExecutionProvider"
    # 확인할 수 없으면 단정하지 않는다.
    assert active_provider(_ModelWithoutSession()) == ""


def test_CUDA가_실제로_붙으면_ctx_id는_0이다() -> None:
    # 컨테이너에는 GPU 한 장만 넘어오므로 그것이 0번이다. 물리 번호를 앱이 고르지 않는다.
    assert verify_execution_provider(_Model("CUDAExecutionProvider"), "auto") == 0
    assert verify_execution_provider(_Model("CUDAExecutionProvider"), "cuda") == 0


def test_cuda로_지정했는데_CPU로_내려가면_기동에_실패한다() -> None:
    # **이것이 이번 변경의 핵심이다.** 예전에는 조용히 CPU로 돌았고 아무도 몰랐다.
    with pytest.raises(RuntimeError, match="CPUExecutionProvider"):
        verify_execution_provider(_Model("CPUExecutionProvider"), "cuda")


def test_auto면_CPU로_내려가되_경고를_남긴다(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        ctx_id = verify_execution_provider(_Model("CPUExecutionProvider"), "auto")

    # CPU는 insightface에서 -1이다. provider와 ctx_id가 어긋나면 안 된다.
    assert ctx_id == -1
    assert any("CPU" in record.message or "CPU" in str(record.args) for record in caplog.records)


def test_cpu로_지정하면_경고_없이_ctx_id가_음수다(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        ctx_id = verify_execution_provider(_Model("CPUExecutionProvider"), "cpu")

    assert ctx_id == -1
    # 일부러 고른 CPU를 경고할 이유가 없다.
    assert not caplog.records
