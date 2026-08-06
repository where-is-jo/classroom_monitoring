"""사용자 기능의 명시적 오류."""

from __future__ import annotations

from ..shared.errors import DomainError


class UserNotFoundError(DomainError):
    code = "USER_NOT_FOUND"
    status_code = 404

    def __init__(self) -> None:
        super().__init__("요청한 사용자를 찾을 수 없습니다.")


class UserEmailConflictError(DomainError):
    code = "USER_EMAIL_CONFLICT"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("이미 사용 중인 이메일입니다.")


class UserConcurrentUpdateError(DomainError):
    code = "USER_CONCURRENT_UPDATE"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("사용자 정보가 이미 변경되었습니다. 새로고침 후 다시 시도하세요.")


class UserOperationConflictError(DomainError):
    code = "USER_OPERATION_CONFLICT"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("같은 operation_id가 다른 변경에 사용되었습니다.")


class InvalidEmailError(DomainError):
    code = "INVALID_EMAIL"
    status_code = 400

    def __init__(self) -> None:
        super().__init__("이메일 형식이 올바르지 않습니다.")


class InvalidUserNameError(DomainError):
    code = "INVALID_USER_NAME"
    status_code = 400

    def __init__(self) -> None:
        super().__init__("사용자 이름은 비어 있을 수 없습니다.")


class PasswordPolicyError(DomainError):
    code = "PASSWORD_POLICY_VIOLATION"
    status_code = 400

    def __init__(self, violations: tuple[str, ...]) -> None:
        super().__init__(
            "비밀번호 정책을 충족하지 않습니다.",
            details={"requirements": list(violations)},
        )


class CurrentPasswordMismatchError(DomainError):
    code = "CURRENT_PASSWORD_MISMATCH"
    status_code = 400

    def __init__(self) -> None:
        super().__init__("현재 비밀번호가 올바르지 않습니다.")


class PasswordUnchangedError(DomainError):
    code = "PASSWORD_UNCHANGED"
    status_code = 400

    def __init__(self) -> None:
        super().__init__("새 비밀번호는 현재 비밀번호와 달라야 합니다.")


class UnsupportedUserRoleError(DomainError):
    code = "UNSUPPORTED_USER_ROLE"
    status_code = 400

    def __init__(self) -> None:
        super().__init__("학생, 직원, 관리자 역할만 선택할 수 있습니다.")


class SelfDeactivationError(DomainError):
    code = "SELF_DEACTIVATION_FORBIDDEN"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("현재 로그인한 계정은 비활성화할 수 없습니다.")
