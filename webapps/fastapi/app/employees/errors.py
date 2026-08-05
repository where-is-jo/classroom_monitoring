"""직원 프로필과 상태 정책의 명시적 도메인 오류."""

from __future__ import annotations

from ..shared.errors import DomainError


class EmployeeNotFoundError(DomainError):
    code = "EMPLOYEE_NOT_FOUND"
    status_code = 404

    def __init__(self) -> None:
        super().__init__("요청한 직원을 찾을 수 없습니다.")


class EmployeeNumberConflictError(DomainError):
    code = "EMPLOYEE_NUMBER_CONFLICT"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("이미 사용 중인 직원 번호입니다.")


class EmployeeUserLinkConflictError(DomainError):
    code = "EMPLOYEE_USER_LINK_CONFLICT"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("해당 STAFF 계정은 이미 다른 직원과 연결되어 있습니다.")


class InvalidEmployeeUserError(DomainError):
    code = "INVALID_EMPLOYEE_USER"
    status_code = 400

    def __init__(self) -> None:
        super().__init__("직원에는 STAFF 역할 사용자만 연결할 수 있습니다.")


class InvalidEmployeeProfileError(DomainError):
    code = "INVALID_EMPLOYEE_PROFILE"
    status_code = 400

    def __init__(self) -> None:
        super().__init__("직원 프로필 값이 올바르지 않습니다.")


class EmployeeConcurrentUpdateError(DomainError):
    code = "EMPLOYEE_CONCURRENT_UPDATE"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("직원 정보가 이미 변경되었습니다. 새로고침 후 다시 시도하세요.")


class EmployeeOperationConflictError(DomainError):
    code = "EMPLOYEE_OPERATION_CONFLICT"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("같은 operation_id가 다른 직원 변경에 사용되었습니다.")


class EmployeeInactiveError(DomainError):
    code = "EMPLOYEE_INACTIVE"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("비활성 직원의 상태는 변경할 수 없습니다.")


class InvalidStatusOverrideError(DomainError):
    code = "INVALID_STATUS_OVERRIDE"
    status_code = 400

    def __init__(self) -> None:
        super().__init__("수동 override는 미래 종료 시각의 AWAY 또는 OFFSITE만 허용합니다.")
