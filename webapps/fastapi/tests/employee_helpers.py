"""직원 상태 테스트용 외부 의존 없는 조립 helper."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from app.audit.service import AuditService
from app.employees.adapters.memory_repository import InMemoryEmployeeRepository
from app.employees.models import CreateEmployeeCommand, Employee
from app.employees.service import EmployeeService
from app.users.models import User, UserRole
from tests.auth_helpers import AuthStack, build_auth_stack


@dataclass
class EmployeeStack:
    auth: AuthStack
    employees: InMemoryEmployeeRepository
    service: EmployeeService
    admin: User
    staff: User
    student: User

    def create_employee(
        self,
        *,
        employee_no: str = "EMP-001",
        user_id: str | None = None,
        display_name: str = "가상 직원",
    ) -> Employee:
        return self.service.create_employee(
            self.admin,
            CreateEmployeeCommand(
                employee_no=employee_no,
                user_id=user_id,
                display_name=display_name,
                department="플랫폼팀",
                position="연구원",
                office_zone="A-101",
                operation_id=str(uuid4()),
            ),
            ip_fingerprint="test-ip-fingerprint",
        )


def build_employee_stack(
    repository: InMemoryEmployeeRepository | None = None,
) -> EmployeeStack:
    auth = build_auth_stack()
    employees = repository or InMemoryEmployeeRepository()
    service = EmployeeService(
        employees,
        auth.users,
        AuditService(auth.audit, clock=auth.clock),
        away_after_seconds=180,
        offsite_after_seconds=3600,
        clock=auth.clock,
    )
    return EmployeeStack(
        auth=auth,
        employees=employees,
        service=service,
        admin=auth.seed(UserRole.ADMIN, email="employee-admin@example.invalid"),
        staff=auth.seed(UserRole.STAFF, email="employee-staff@example.invalid"),
        student=auth.seed(UserRole.STUDENT, email="employee-student@example.invalid"),
    )
