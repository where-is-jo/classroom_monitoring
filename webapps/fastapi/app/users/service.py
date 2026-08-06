"""사용자 관리 비즈니스 규칙."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from uuid import uuid4

from ..audit.service import AuditService
from ..auth.errors import PermissionDeniedError
from ..auth.ports import AuthRepository
from ..shared.security import (
    PasswordSecurity,
    canonicalize_email,
    is_valid_email,
    validate_password_policy,
)
from .errors import (
    CurrentPasswordMismatchError,
    InvalidEmailError,
    InvalidUserNameError,
    PasswordPolicyError,
    PasswordUnchangedError,
    SelfDeactivationError,
    UnsupportedUserRoleError,
    UserConcurrentUpdateError,
    UserNotFoundError,
    UserOperationConflictError,
)
from .models import (
    ADMIN_ROLES,
    PRODUCT_ROLES,
    ChangePasswordCommand,
    CreateUserCommand,
    UpdateUserCommand,
    User,
    UserPage,
    UserRole,
    UserStatus,
)
from .ports import StaffAssignmentPolicy, UserRepository


class UserService:
    def __init__(
        self,
        repository: UserRepository,
        auth_repository: AuthRepository,
        audit_service: AuditService,
        password_security: PasswordSecurity,
        *,
        password_min_length: int,
        clock: Callable[[], datetime],
        staff_assignment_policy: StaffAssignmentPolicy | None = None,
    ) -> None:
        self._repository = repository
        self._auth_repository = auth_repository
        self._audit_service = audit_service
        self._password_security = password_security
        self._password_min_length = password_min_length
        self._clock = clock
        self._staff_assignments = staff_assignment_policy

    def list_users(
        self,
        actor: User,
        *,
        limit: int,
        offset: int,
        role: UserRole | None = None,
        status: UserStatus | None = None,
        search: str | None = None,
    ) -> UserPage:
        self._require_admin(actor)
        return self._repository.list_users(
            limit=limit,
            offset=offset,
            role=role,
            status=status,
            search=search,
        )

    def get_user(self, actor: User, user_id: str) -> User:
        self._require_admin(actor)
        return self._get_required_user(user_id)

    def create_user(
        self,
        actor: User,
        command: CreateUserCommand,
        *,
        ip_fingerprint: str | None,
    ) -> User:
        self._require_admin(actor)
        self._require_product_role(command.role)
        return self._create(
            command,
            actor_user_id=actor.id,
            action="USER_CREATED",
            ip_fingerprint=ip_fingerprint,
            must_change_password=True,
        )

    def seed_user(self, command: CreateUserCommand) -> User:
        existing = self._repository.get_user_by_email(canonicalize_email(command.email))
        if existing is not None:
            return existing
        return self._create(
            command,
            actor_user_id=None,
            action="USER_SEEDED",
            ip_fingerprint=None,
            must_change_password=False,
        )

    def update_user(
        self,
        actor: User,
        command: UpdateUserCommand,
        *,
        ip_fingerprint: str | None,
    ) -> User:
        self._require_admin(actor)
        idempotent_user = self._idempotent_result(command.operation_id, command.user_id)
        if idempotent_user is not None:
            self._unlink_invalid_staff(
                actor,
                idempotent_user,
                operation_id=command.operation_id,
                ip_fingerprint=ip_fingerprint,
            )
            return idempotent_user

        current = self._get_required_user(command.user_id)
        if current.version != command.expected_version:
            raise UserConcurrentUpdateError()
        email = current.email
        if command.email is not None:
            email = self._validated_email(command.email)
        name = current.name if command.name is None else command.name.strip()
        if not name:
            raise InvalidUserNameError()
        role = current.role if command.role is None else command.role
        if command.role is not None:
            self._require_product_role(command.role)
        status = current.status if command.status is None else command.status
        self._protect_self(actor, current, status=status)

        now = self._clock()
        updated = replace(
            current,
            email=email,
            name=name,
            role=role,
            status=status,
            failed_login_count=0 if status == UserStatus.ACTIVE else current.failed_login_count,
            locked_until=None if status == UserStatus.ACTIVE else current.locked_until,
            updated_at=now,
            version=current.version + 1,
            last_operation_id=command.operation_id,
        )
        saved = self._repository.replace_user(updated, expected_version=current.version)
        if saved is None:
            raise UserConcurrentUpdateError()
        if status != UserStatus.ACTIVE:
            self._auth_repository.revoke_user_tokens(saved.id, now=now)
        self._record_user_audit(
            operation_id=command.operation_id,
            actor_user_id=actor.id,
            action="USER_UPDATED",
            before=current,
            after=saved,
            ip_fingerprint=ip_fingerprint,
        )
        self._unlink_invalid_staff(
            actor,
            saved,
            operation_id=command.operation_id,
            ip_fingerprint=ip_fingerprint,
        )
        return saved

    def deactivate_user(
        self,
        actor: User,
        user_id: str,
        *,
        operation_id: str,
        ip_fingerprint: str | None,
    ) -> User:
        self._require_admin(actor)
        idempotent_user = self._idempotent_result(operation_id, user_id)
        if idempotent_user is not None:
            self._unlink_invalid_staff(
                actor,
                idempotent_user,
                operation_id=operation_id,
                ip_fingerprint=ip_fingerprint,
            )
            return idempotent_user
        current = self._get_required_user(user_id)
        if current.status == UserStatus.INACTIVE:
            self._unlink_invalid_staff(
                actor,
                current,
                operation_id=operation_id,
                ip_fingerprint=ip_fingerprint,
            )
            return current
        self._protect_self(actor, current, status=UserStatus.INACTIVE)
        now = self._clock()
        inactive = replace(
            current,
            status=UserStatus.INACTIVE,
            updated_at=now,
            version=current.version + 1,
            last_operation_id=operation_id,
        )
        saved = self._repository.replace_user(inactive, expected_version=current.version)
        if saved is None:
            raise UserConcurrentUpdateError()
        self._auth_repository.revoke_user_tokens(saved.id, now=now)
        self._record_user_audit(
            operation_id=operation_id,
            actor_user_id=actor.id,
            action="USER_DEACTIVATED",
            before=current,
            after=saved,
            ip_fingerprint=ip_fingerprint,
        )
        self._unlink_invalid_staff(
            actor,
            saved,
            operation_id=operation_id,
            ip_fingerprint=ip_fingerprint,
        )
        return saved

    def _unlink_invalid_staff(
        self,
        actor: User,
        user: User,
        *,
        operation_id: str,
        ip_fingerprint: str | None,
    ) -> None:
        if self._staff_assignments is not None and (
            user.role != UserRole.STAFF or user.status != UserStatus.ACTIVE
        ):
            self._staff_assignments.unlink_staff_user(
                actor,
                user.id,
                operation_id=operation_id,
                ip_fingerprint=ip_fingerprint,
            )

    def change_password(
        self,
        actor: User,
        command: ChangePasswordCommand,
        *,
        ip_fingerprint: str | None,
    ) -> User:
        idempotent_user = self._idempotent_result(command.operation_id, actor.id)
        if idempotent_user is not None:
            return idempotent_user
        current = self._get_required_user(actor.id)
        if not self._password_security.verify_password(
            command.current_password, current.password_hash
        ):
            raise CurrentPasswordMismatchError()
        if self._password_security.verify_password(command.new_password, current.password_hash):
            raise PasswordUnchangedError()
        self._validate_password(command.new_password)
        now = self._clock()
        changed = replace(
            current,
            password_hash=self._password_security.hash_password(command.new_password),
            must_change_password=False,
            password_changed_at=now,
            updated_at=now,
            version=current.version + 1,
            last_operation_id=command.operation_id,
        )
        saved = self._repository.replace_user(changed, expected_version=current.version)
        if saved is None:
            raise UserConcurrentUpdateError()
        self._auth_repository.revoke_user_tokens(saved.id, now=now)
        self._record_user_audit(
            operation_id=command.operation_id,
            actor_user_id=actor.id,
            action="USER_PASSWORD_CHANGED",
            before=current,
            after=saved,
            ip_fingerprint=ip_fingerprint,
        )
        return saved

    def _create(
        self,
        command: CreateUserCommand,
        *,
        actor_user_id: str | None,
        action: str,
        ip_fingerprint: str | None,
        must_change_password: bool,
    ) -> User:
        existing_operation = self._repository.get_user_by_operation_id(command.operation_id)
        if existing_operation is not None:
            if existing_operation.email != canonicalize_email(command.email):
                raise UserOperationConflictError()
            return existing_operation
        email = self._validated_email(command.email)
        self._validate_password(command.password)
        now = self._clock()
        user = User(
            id=command.entity_id or str(uuid4()),
            email=email,
            password_hash=self._password_security.hash_password(command.password),
            name=command.name.strip(),
            role=command.role,
            status=UserStatus.ACTIVE,
            failed_login_count=0,
            locked_until=None,
            last_login_at=None,
            created_at=now,
            updated_at=now,
            version=0,
            created_operation_id=command.operation_id,
            last_operation_id=command.operation_id,
            must_change_password=must_change_password,
            password_changed_at=None,
        )
        if not user.name:
            raise InvalidUserNameError()
        saved = self._repository.create_user(user)
        self._record_user_audit(
            operation_id=command.operation_id,
            actor_user_id=actor_user_id,
            action=action,
            before=None,
            after=saved,
            ip_fingerprint=ip_fingerprint,
        )
        return saved

    def _idempotent_result(self, operation_id: str, user_id: str) -> User | None:
        audit_log = self._audit_service.get_by_operation_id(operation_id)
        if audit_log is None:
            return None
        if audit_log.resource_type != "user" or audit_log.resource_id != user_id:
            raise UserOperationConflictError()
        return self._get_required_user(user_id)

    def _record_user_audit(
        self,
        *,
        operation_id: str,
        actor_user_id: str | None,
        action: str,
        before: User | None,
        after: User,
        ip_fingerprint: str | None,
    ) -> None:
        self._audit_service.record(
            operation_id=operation_id,
            actor_user_id=actor_user_id,
            action=action,
            resource_type="user",
            resource_id=after.id,
            before=_audit_user_state(before),
            after=_audit_user_state(after),
            ip_fingerprint=ip_fingerprint,
        )

    def _protect_self(
        self,
        actor: User,
        current: User,
        *,
        status: UserStatus,
    ) -> None:
        if actor.id == current.id and status != UserStatus.ACTIVE:
            raise SelfDeactivationError()

    @staticmethod
    def _require_admin(actor: User) -> None:
        if actor.role not in ADMIN_ROLES or actor.status != UserStatus.ACTIVE:
            raise PermissionDeniedError()

    def _get_required_user(self, user_id: str) -> User:
        user = self._repository.get_user(user_id)
        if user is None:
            raise UserNotFoundError()
        return user

    def _validated_email(self, email: str) -> str:
        canonical_email = canonicalize_email(email)
        if not is_valid_email(canonical_email):
            raise InvalidEmailError()
        return canonical_email

    def _validate_password(self, password: str) -> None:
        violations = validate_password_policy(
            password,
            minimum_length=self._password_min_length,
        )
        if violations:
            raise PasswordPolicyError(violations)

    @staticmethod
    def _require_product_role(role: UserRole) -> None:
        if role not in PRODUCT_ROLES:
            raise UnsupportedUserRoleError()


def _audit_user_state(user: User | None) -> dict[str, str]:
    if user is None:
        return {}
    return {"role": user.role.value, "status": user.status.value}
