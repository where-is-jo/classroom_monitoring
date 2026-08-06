"""EmployeeRepository의 외부 의존 없는 memory 구현."""

from __future__ import annotations

from threading import RLock

from ..errors import (
    EmployeeNumberConflictError,
    EmployeeOperationConflictError,
    EmployeeUserLinkConflictError,
)
from ..models import (
    Employee,
    EmployeeObservation,
    EmployeePage,
    EmployeeStatus,
    EmployeeStatusHistory,
    EmployeeStatusHistoryPage,
    StatusSource,
)


class InMemoryEmployeeRepository:
    def __init__(self) -> None:
        self._employees: dict[str, Employee] = {}
        self._history_by_operation: dict[str, EmployeeStatusHistory] = {}
        self._observations: dict[str, EmployeeObservation] = {}
        self._lock = RLock()

    def list_employees(
        self,
        *,
        limit: int,
        offset: int,
        search: str | None,
        department: str | None,
        status: EmployeeStatus | None,
        is_active: bool | None,
    ) -> EmployeePage:
        with self._lock:
            employees = list(self._employees.values())
        if search:
            normalized = search.strip().lower()
            employees = [
                employee
                for employee in employees
                if normalized in employee.employee_no.lower()
                or normalized in employee.display_name.lower()
            ]
        if department:
            normalized_department = department.strip().lower()
            employees = [
                employee
                for employee in employees
                if employee.department.lower() == normalized_department
            ]
        if status is not None:
            employees = [
                employee
                for employee in employees
                if employee.current_status.status == status
            ]
        if is_active is not None:
            employees = [
                employee for employee in employees if employee.is_active == is_active
            ]
        employees.sort(key=lambda employee: (employee.display_name, employee.id))
        return EmployeePage(
            items=employees[offset : offset + limit],
            total=len(employees),
        )

    def list_active_employees(self) -> list[Employee]:
        with self._lock:
            employees = [employee for employee in self._employees.values() if employee.is_active]
        return sorted(employees, key=lambda employee: employee.id)

    def get_employee(self, employee_id: str) -> Employee | None:
        with self._lock:
            return self._employees.get(employee_id)

    def dashboard_snapshot(
        self,
    ) -> tuple[list[Employee], list[EmployeeStatusHistory]]:
        """Return an immutable-value snapshot for the local admin read model."""
        with self._lock:
            return (
                list(self._employees.values()),
                list(self._history_by_operation.values()),
            )

    def get_employee_by_number(self, employee_no: str) -> Employee | None:
        with self._lock:
            return next(
                (
                    employee
                    for employee in self._employees.values()
                    if employee.employee_no == employee_no
                ),
                None,
            )

    def get_employee_by_user_id(self, user_id: str) -> Employee | None:
        with self._lock:
            return next(
                (
                    employee
                    for employee in self._employees.values()
                    if employee.user_id == user_id
                ),
                None,
            )

    def get_employee_by_operation_id(self, operation_id: str) -> Employee | None:
        with self._lock:
            return next(
                (
                    employee
                    for employee in self._employees.values()
                    if operation_id in employee.operation_ids
                ),
                None,
            )

    def create_employee(
        self,
        employee: Employee,
        initial_history: EmployeeStatusHistory,
    ) -> Employee:
        with self._lock:
            operation_owner = self.get_employee_by_operation_id(
                employee.created_operation_id
            )
            if operation_owner is not None:
                if operation_owner.employee_no != employee.employee_no:
                    raise EmployeeOperationConflictError()
                self._append_history(initial_history)
                return operation_owner
            if self.get_employee_by_number(employee.employee_no) is not None:
                raise EmployeeNumberConflictError()
            self._ensure_user_link_available(employee.user_id, employee.id)
            self._employees[employee.id] = employee
            self._append_history(initial_history)
            return employee

    def replace_employee(
        self,
        employee: Employee,
        *,
        expected_version: int,
        history: EmployeeStatusHistory | None,
    ) -> Employee | None:
        with self._lock:
            operation_owner = self.get_employee_by_operation_id(
                employee.last_operation_id
            )
            history_owner = self.get_history_by_operation_id(employee.last_operation_id)
            if operation_owner is not None:
                if operation_owner.id != employee.id:
                    raise EmployeeOperationConflictError()
                if history is not None:
                    self._append_history(history)
                return operation_owner
            if history_owner is not None:
                if history_owner.employee_id != employee.id:
                    raise EmployeeOperationConflictError()
                return self._employees.get(employee.id)

            current = self._employees.get(employee.id)
            if current is None or current.version != expected_version:
                return None
            number_owner = self.get_employee_by_number(employee.employee_no)
            if number_owner is not None and number_owner.id != employee.id:
                raise EmployeeNumberConflictError()
            self._ensure_user_link_available(employee.user_id, employee.id)
            self._employees[employee.id] = employee
            if history is not None:
                self._append_history(history)
            return employee

    def list_status_history(
        self,
        employee_id: str,
        *,
        limit: int,
        offset: int,
        source: StatusSource | None,
        from_status: EmployeeStatus | None,
        to_status: EmployeeStatus | None,
    ) -> EmployeeStatusHistoryPage:
        with self._lock:
            items = [
                item
                for item in self._history_by_operation.values()
                if item.employee_id == employee_id
            ]
        if source is not None:
            items = [item for item in items if item.source == source]
        if from_status is not None:
            items = [item for item in items if item.from_status == from_status]
        if to_status is not None:
            items = [item for item in items if item.to_status == to_status]
        items.sort(key=lambda item: (item.occurred_at, item.id), reverse=True)
        return EmployeeStatusHistoryPage(
            items=items[offset : offset + limit],
            total=len(items),
        )

    def get_history_by_operation_id(
        self,
        operation_id: str,
    ) -> EmployeeStatusHistory | None:
        with self._lock:
            return self._history_by_operation.get(operation_id)

    def get_observation(self, event_id: str) -> EmployeeObservation | None:
        with self._lock:
            return self._observations.get(event_id)

    def create_observation(
        self,
        observation: EmployeeObservation,
    ) -> EmployeeObservation:
        with self._lock:
            existing = self._observations.get(observation.event_id)
            if existing is not None:
                return existing
            self._observations[observation.event_id] = observation
            return observation

    def get_latest_observation(self, employee_id: str) -> EmployeeObservation | None:
        return self._latest_observation(employee_id, person_present=None)

    def get_latest_present_observation(
        self,
        employee_id: str,
    ) -> EmployeeObservation | None:
        return self._latest_observation(employee_id, person_present=True)

    def _latest_observation(
        self,
        employee_id: str,
        *,
        person_present: bool | None,
    ) -> EmployeeObservation | None:
        with self._lock:
            observations = [
                observation
                for observation in self._observations.values()
                if observation.employee_id == employee_id
                and (
                    person_present is None
                    or observation.person_present == person_present
                )
            ]
        if not observations:
            return None
        return max(
            observations,
            key=lambda observation: (
                observation.observed_at,
                observation.received_at,
                observation.event_id,
            ),
        )

    def _append_history(self, history: EmployeeStatusHistory) -> None:
        existing = self._history_by_operation.get(history.operation_id)
        if existing is not None:
            if existing.employee_id != history.employee_id:
                raise EmployeeOperationConflictError()
            return
        self._history_by_operation[history.operation_id] = history

    def _ensure_user_link_available(
        self,
        user_id: str | None,
        employee_id: str,
    ) -> None:
        if user_id is None:
            return
        owner = self.get_employee_by_user_id(user_id)
        if owner is not None and owner.id != employee_id:
            raise EmployeeUserLinkConflictError()
