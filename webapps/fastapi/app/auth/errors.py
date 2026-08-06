"""인증·인가 오류."""

from __future__ import annotations

from ..shared.errors import DomainError


class InvalidCredentialsError(DomainError):
    code = "INVALID_CREDENTIALS"
    status_code = 401

    def __init__(self) -> None:
        super().__init__("이메일 또는 비밀번호가 올바르지 않습니다.")


class AccountLockedError(DomainError):
    code = "ACCOUNT_LOCKED"
    status_code = 401

    def __init__(self) -> None:
        super().__init__("계정을 일시적으로 사용할 수 없습니다.")


class AuthenticationRequiredError(DomainError):
    code = "AUTHENTICATION_REQUIRED"
    status_code = 401

    def __init__(self) -> None:
        super().__init__("로그인이 필요합니다.")


class PermissionDeniedError(DomainError):
    code = "FORBIDDEN"
    status_code = 403

    def __init__(self) -> None:
        super().__init__("이 요청을 수행할 권한이 없습니다.")


class InvalidRefreshTokenError(DomainError):
    code = "INVALID_REFRESH_TOKEN"
    status_code = 401

    def __init__(self) -> None:
        super().__init__("세션을 갱신할 수 없습니다. 다시 로그인하세요.")


class RefreshTokenReuseError(DomainError):
    code = "REFRESH_TOKEN_REUSE_DETECTED"
    status_code = 401

    def __init__(self) -> None:
        super().__init__("세션 재사용이 감지되어 다시 로그인이 필요합니다.")


class LoginRateLimitedError(DomainError):
    code = "LOGIN_RATE_LIMITED"
    status_code = 429

    def __init__(self) -> None:
        super().__init__("로그인 시도가 너무 많습니다. 잠시 후 다시 시도하세요.")


class InvalidOriginError(DomainError):
    code = "INVALID_ORIGIN"
    status_code = 403

    def __init__(self) -> None:
        super().__init__("허용되지 않은 요청 출처입니다.")


class CsrfValidationError(DomainError):
    code = "CSRF_VALIDATION_FAILED"
    status_code = 403

    def __init__(self) -> None:
        super().__init__("요청 검증에 실패했습니다. 페이지를 새로고침하세요.")


class PageAuthenticationRequired(Exception):
    def __init__(self, return_to: str) -> None:
        super().__init__(return_to)
        self.return_to = return_to
