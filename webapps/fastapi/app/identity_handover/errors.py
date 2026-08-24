"""신원 인계 설정 오류."""

from ..shared.errors import DomainError


class IdentityHandoverRouteNotFoundError(DomainError):
    code = "IDENTITY_HANDOVER_ROUTE_NOT_FOUND"
    status_code = 404

    def __init__(self, message: str = "신원 인계 설정을 찾을 수 없습니다.") -> None:
        super().__init__(message)


class IdentityHandoverRouteInputError(DomainError):
    code = "IDENTITY_HANDOVER_ROUTE_INPUT_INVALID"
    status_code = 422

    def __init__(self, message: str) -> None:
        super().__init__(message)
