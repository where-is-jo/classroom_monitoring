"""직원 집계의 프로세스 외부 저장소 포트."""

from __future__ import annotations

from typing import Protocol

from .models import (
    Employee,
    EmployeeObservation,
    EmployeePage,
    EmployeeStatus,
    EmployeeStatusHistory,
    EmployeeStatusHistoryPage,
    StatusSource,
)


class EmployeeRepository(Protocol):
    def list_employees(
        self,
        *,
        limit: int,
        offset: int,
        search: str | None,
        department: str | None,
        status: EmployeeStatus | None,
        is_active: bool | None,
    ) -> EmployeePage: ...

    def list_active_employees(self) -> list[Employee]: ...

    def get_employee(self, employee_id: str) -> Employee | None: ...

    def get_employee_by_number(self, employee_no: str) -> Employee | None: ...

    def get_employee_by_user_id(self, user_id: str) -> Employee | None: ...

    def get_employee_by_operation_id(self, operation_id: str) -> Employee | None: ...

    def create_employee(
        self,
        employee: Employee,
        initial_history: EmployeeStatusHistory,
    ) -> Employee: ...

    def replace_employee(
        self,
        employee: Employee,
        *,
        expected_version: int,
        history: EmployeeStatusHistory | None,
    ) -> Employee | None: ...

    def list_status_history(
        self,
        employee_id: str,
        *,
        limit: int,
        offset: int,
        source: StatusSource | None,
        from_status: EmployeeStatus | None,
        to_status: EmployeeStatus | None,
    ) -> EmployeeStatusHistoryPage: ...

    def get_history_by_operation_id(
        self,
        operation_id: str,
    ) -> EmployeeStatusHistory | None: ...

    def get_observation(self, event_id: str) -> EmployeeObservation | None: ...

    def create_observation(
        self,
        observation: EmployeeObservation,
    ) -> EmployeeObservation: ...

    def get_latest_observation(self, employee_id: str) -> EmployeeObservation | None: ...

    def get_latest_present_observation(
        self,
        employee_id: str,
    ) -> EmployeeObservation | None: ...
