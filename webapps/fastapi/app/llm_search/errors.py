"""자연어 검색의 도메인 예외.

**오류 본문에 LLM 원문을 넣지 않는다.** 모델이 무엇을 뱉었든 그것은 검증되지 않은
외부 입력이고, 그대로 응답에 실으면 프롬프트에 넣은 내부 정보(카메라 목록·강의실
식별자)가 되돌아 나올 수 있다. 대신 우리가 정의한 사유 코드만 `details.reason`에
담는다. 원문은 서버 로그에만 남긴다.
"""

from __future__ import annotations

from ..shared.errors import DomainError


class LlmSearchDisabledError(DomainError):
    """이 배포에서 자연어 검색을 제공하지 않는다.

    **"닿지 못했다"와 다르다.** 서버가 잠시 죽은 것이 아니라 애초에 없는 것이므로
    기다리거나 다시 시도할 이유가 없다. 세 상태(비활성 / 닿지 못함 / 조건으로 바꾸지
    못함)를 코드로 구분해야 로그에서도 섞이지 않는다.

    501이 아니라 503인 이유는 구현이 없는 것이 아니라 **의존 서비스(LLM)가 이 환경에
    없기** 때문이다. api-convention의 503("의존 서비스 사용 불가")이 그 자리다.
    """

    code = "LLM_SEARCH_DISABLED"
    status_code = 503

    def __init__(self) -> None:
        super().__init__("자연어 검색은 이 환경에서 제공하지 않습니다.")


class LlmSearchPlannerUnavailableError(DomainError):
    """LLM 서버에 닿지 못했다.

    **"조건을 만들지 못했다"와 다르다.** 이건 모델에게 물어보지도 못한 상태이므로
    질문을 고쳐도 해결되지 않는다. 화면에서 둘을 구분해 보여줘야 사용자가 질문을
    다시 쓸지 관리자를 부를지 판단할 수 있다.
    """

    code = "LLM_SEARCH_PLANNER_UNAVAILABLE"
    status_code = 503

    def __init__(self) -> None:
        super().__init__("자연어 검색 서버를 일시적으로 사용할 수 없습니다.")


class LlmSearchPlanInvalidError(DomainError):
    """모델이 규격에 맞지 않는 응답을 냈다.

    사용자 잘못이 아닐 수 있지만 사용자가 할 수 있는 일은 질문을 다시 쓰는 것뿐이라
    422로 돌려준다.
    """

    code = "LLM_SEARCH_PLAN_INVALID"
    status_code = 422

    def __init__(self, reason: str) -> None:
        super().__init__(
            "질문을 검색 조건으로 바꾸지 못했습니다. 기간과 대상을 더 분명하게 적어 주세요.",
            details={"reason": reason},
        )
