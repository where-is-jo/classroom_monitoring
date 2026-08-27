"""얼굴 모델을 CPU로 돌릴지 GPU로 돌릴지 정한다.

**app.py에서 떼어낸 이유는 검증 때문이다.** app.py는 module import 시점에 mediapipe와
insightface를 요구해서, 그 둘이 없는 곳에서는 이 판단 로직만 따로 시험할 수 없었다.
여기에는 표준 라이브러리와 onnxruntime만 있다.

이 파일이 막으려는 실패는 하나다 — **조용히 CPU로 내려가는 것.** onnxruntime은 의존
라이브러리가 없어 CUDA provider 로드에 실패해도 `get_available_providers()` 목록에는
그대로 두고 세션만 CPU로 만든다. 실제로 그 상태로 오래 돌았고, 지표에는 "얼굴 분석이
느리다"로만 보였다(CPU 63.8ms vs CUDA 3.8ms, GPU 서버 실측).
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["active_provider", "requested_providers", "verify_execution_provider"]


def requested_providers() -> tuple[str, list[str]]:
    """`FACE_EXECUTION_PROVIDER` 설정과 요청할 provider 목록을 돌려준다.

    | 값 | 동작 |
    | --- | --- |
    | `auto`(기본) | CUDA를 요청하고, 실제로 안 되면 경고를 남기고 CPU로 돈다 |
    | `cuda` | CUDA로 실행하지 못하면 기동에 실패한다 |
    | `cpu` | 항상 CPU |

    기본값이 `auto`인 이유는 GPU가 없는 곳에서도 이 서비스가 떠야 하기 때문이다.
    """
    choice = os.environ.get("FACE_EXECUTION_PROVIDER", "auto").strip().lower()
    if choice not in {"auto", "cuda", "cpu"}:
        raise RuntimeError(
            "FACE_EXECUTION_PROVIDER는 auto, cuda, cpu 중 하나여야 합니다: " + choice
        )
    if choice == "cpu":
        return choice, ["CPUExecutionProvider"]
    return choice, ["CUDAExecutionProvider", "CPUExecutionProvider"]


def active_provider(model: Any) -> str:
    """그 모델이 **실제로** 쓰는 provider. 확인할 수 없으면 빈 문자열.

    **요청한 provider와 다를 수 있다.** onnxruntime은 의존 라이브러리가 없어 CUDA
    provider 로드에 실패해도 `get_available_providers()` 목록에는 그대로 두고,
    세션만 조용히 CPU로 내려간다. 실제로 `libcublasLt.so.13`이 없는 이미지에서
    목록에는 CUDA가 보였고 속도는 CPU 그대로였다. 그래서 목록이 아니라 세션에 묻는다.
    """
    session = getattr(model, "session", None)
    providers = getattr(session, "get_providers", None)
    if providers is None:
        return ""
    active = providers()
    return str(active[0]) if active else ""


def verify_execution_provider(model: Any, choice: str) -> int:
    """실제 provider를 확인하고 insightface `ctx_id`를 정한다.

    **provider와 `ctx_id`는 반드시 같이 움직인다.** insightface는 `ctx_id=-1`이면 CPU,
    0 이상이면 그 번호의 GPU를 쓴다. 세션은 CPU인데 `ctx_id=0`을 주면 어긋난다.

    `ctx_id`는 **컨테이너가 보는 번호**다. compose가 `device_ids: ["1"]`로 GPU 한 장만
    넘기므로 컨테이너 안에서는 그것이 0번이다. 앱이 물리 번호를 고르지 않는다 —
    고르게 두면 다른 팀이 쓰는 카드를 잡을 수 있다.
    """
    active = active_provider(model)
    if active == "CUDAExecutionProvider":
        logger.info("얼굴 모델을 CUDA로 실행한다 (컨테이너가 보는 GPU 0번).")
        return 0
    if choice == "cuda":
        raise RuntimeError(
            "FACE_EXECUTION_PROVIDER=cuda인데 실제 실행 provider가 "
            f"{active or '알 수 없음'}입니다. onnxruntime-gpu의 CUDA 의존성"
            "(extras에 빠져 있는 nvidia-cublas 포함)과 LD_LIBRARY_PATH, "
            "컨테이너 GPU 예약을 확인하세요."
        )
    if choice == "auto":
        logger.warning(
            "CUDA로 실행하지 못해 얼굴 모델이 %s로 돈다. 얼굴 분석이 수십 배 "
            "느려지고 입구 카메라 갱신 주기가 그만큼 길어진다.",
            active or "CPU",
        )
    return -1
