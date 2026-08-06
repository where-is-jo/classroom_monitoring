"""로그인, JWT 검증, refresh rotation 비즈니스 규칙."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from threading import RLock
from uuid import uuid4

from ..audit.service import AuditService
from ..shared.security import (
    PasswordSecurity,
    TokenExpiredError,
    TokenSecurity,
    TokenValidationError,
    canonicalize_email,
    hash_refresh_token,
)
from ..users.errors import UserConcurrentUpdateError
from ..users.models import PRODUCT_ROLES, User, UserStatus
from ..users.ports import UserRepository
from .errors import (
    AccountLockedError,
    AuthenticationRequiredError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    LoginRateLimitedError,
    RefreshTokenReuseError,
)
from .models import (
    AuthenticatedSession,
    LoginCommand,
    RefreshRotationStatus,
    RefreshToken,
    SessionTokens,
)
from .ports import AuthRepository


class LoginRateLimiter:
    """원문 IP를 보관하지 않는 프로세스 내 짧은 구간 제한기."""

    def __init__(self, *, max_failures: int, window_seconds: int) -> None:
        self._max_failures = max_failures
        self._window = timedelta(seconds=window_seconds)
        self._failures: dict[str, deque[datetime]] = {}
        self._lock = RLock()

    def ensure_allowed(self, ip_fingerprint: str, *, now: datetime) -> None:
        with self._lock:
            failures = self._active_failures(ip_fingerprint, now=now)
            if len(failures) >= self._max_failures:
                raise LoginRateLimitedError()

    def record_failure(self, ip_fingerprint: str, *, now: datetime) -> None:
        with self._lock:
            failures = self._active_failures(ip_fingerprint, now=now)
            failures.append(now)

    def clear(self, ip_fingerprint: str) -> None:
        with self._lock:
            self._failures.pop(ip_fingerprint, None)

    def _active_failures(
        self,
        ip_fingerprint: str,
        *,
        now: datetime,
    ) -> deque[datetime]:
        failures = self._failures.setdefault(ip_fingerprint, deque())
        cutoff = now - self._window
        while failures and failures[0] <= cutoff:
            failures.popleft()
        return failures


class AuthService:
    def __init__(
        self,
        user_repository: UserRepository,
        auth_repository: AuthRepository,
        audit_service: AuditService,
        password_security: PasswordSecurity,
        token_security: TokenSecurity,
        rate_limiter: LoginRateLimiter,
        *,
        account_max_failures: int,
        lockout_seconds: int,
        clock: Callable[[], datetime],
    ) -> None:
        self._users = user_repository
        self._auth = auth_repository
        self._audit = audit_service
        self._password_security = password_security
        self._token_security = token_security
        self._rate_limiter = rate_limiter
        self._account_max_failures = account_max_failures
        self._lockout = timedelta(seconds=lockout_seconds)
        self._clock = clock

    def login(self, command: LoginCommand) -> AuthenticatedSession:
        now = self._clock()
        self._rate_limiter.ensure_allowed(command.ip_fingerprint, now=now)
        user = self._users.get_user_by_email(canonicalize_email(command.email))
        if user is None:
            self._password_security.verify_dummy(command.password)
            self._rate_limiter.record_failure(command.ip_fingerprint, now=now)
            raise InvalidCredentialsError()

        user = self._unlock_if_expired(
            user,
            now=now,
            ip_fingerprint=command.ip_fingerprint,
        )
        if user.status == UserStatus.LOCKED:
            self._password_security.verify_password(command.password, user.password_hash)
            self._rate_limiter.record_failure(command.ip_fingerprint, now=now)
            raise AccountLockedError()
        password_matches = self._password_security.verify_password(
            command.password, user.password_hash
        )
        if (
            user.status != UserStatus.ACTIVE
            or user.role not in PRODUCT_ROLES
            or not password_matches
        ):
            self._rate_limiter.record_failure(command.ip_fingerprint, now=now)
            if user.status == UserStatus.ACTIVE and user.role in PRODUCT_ROLES:
                locked = self._record_account_failure(
                    user,
                    now=now,
                    ip_fingerprint=command.ip_fingerprint,
                )
                if locked:
                    raise AccountLockedError()
            raise InvalidCredentialsError()

        user = self._record_login_success(user, now=now)
        self._rate_limiter.clear(command.ip_fingerprint)
        return self._create_session(user, now=now)

    def refresh(self, raw_refresh_token: str) -> AuthenticatedSession:
        now = self._clock()
        try:
            claims = self._token_security.decode_refresh_token(
                raw_refresh_token,
                now=now,
            )
        except (TokenExpiredError, TokenValidationError):
            raise InvalidRefreshTokenError() from None
        token_hash = hash_refresh_token(raw_refresh_token)
        current = self._auth.get_refresh_token(token_hash)
        if current is None:
            raise InvalidRefreshTokenError()
        if current.revoked_at is not None:
            if current.replaced_by_id is not None:
                self._auth.revoke_family(current.family_id, now=now)
                raise RefreshTokenReuseError()
            raise InvalidRefreshTokenError()
        if (
            current.id != claims.token_id
            or current.user_id != claims.user_id
            or current.family_id != claims.family_id
            or current.expires_at <= now
        ):
            self._auth.revoke_family(current.family_id, now=now)
            raise InvalidRefreshTokenError()

        user = self._users.get_user(current.user_id)
        if (
            user is None
            or user.status != UserStatus.ACTIVE
            or user.role not in PRODUCT_ROLES
        ):
            self._auth.revoke_family(current.family_id, now=now)
            raise InvalidRefreshTokenError()

        access = self._token_security.issue_access_token(user.id, now=now)
        refresh = self._token_security.issue_refresh_token(
            user.id,
            family_id=current.family_id,
            now=now,
        )
        replacement = RefreshToken(
            id=refresh.token_id,
            token_hash=hash_refresh_token(refresh.raw),
            user_id=user.id,
            family_id=current.family_id,
            expires_at=refresh.expires_at,
            created_at=now,
        )
        rotation = self._auth.rotate_refresh_token(
            current_token_hash=token_hash,
            replacement=replacement,
            now=now,
        )
        if rotation.status == RefreshRotationStatus.REUSED:
            raise RefreshTokenReuseError()
        if rotation.status != RefreshRotationStatus.ROTATED:
            raise InvalidRefreshTokenError()
        return AuthenticatedSession(
            user=user,
            tokens=SessionTokens(
                access_token=access.raw,
                refresh_token=refresh.raw,
                access_expires_at=access.expires_at,
                refresh_expires_at=refresh.expires_at,
            ),
        )

    def logout(self, raw_refresh_token: str | None) -> None:
        if not raw_refresh_token:
            return
        current = self._auth.get_refresh_token(hash_refresh_token(raw_refresh_token))
        if current is not None:
            self._auth.revoke_family(current.family_id, now=self._clock())

    def authenticate_access_token(self, raw_access_token: str | None) -> User:
        if not raw_access_token:
            raise AuthenticationRequiredError()
        try:
            claims = self._token_security.decode_access_token(
                raw_access_token,
                now=self._clock(),
            )
        except (TokenExpiredError, TokenValidationError):
            raise AuthenticationRequiredError() from None
        user = self._users.get_user(claims.user_id)
        if (
            user is None
            or user.status != UserStatus.ACTIVE
            or user.role not in PRODUCT_ROLES
        ):
            raise AuthenticationRequiredError()
        return user

    def _create_session(self, user: User, *, now: datetime) -> AuthenticatedSession:
        family_id = str(uuid4())
        access = self._token_security.issue_access_token(user.id, now=now)
        refresh = self._token_security.issue_refresh_token(
            user.id,
            family_id=family_id,
            now=now,
        )
        self._auth.create_refresh_token(
            RefreshToken(
                id=refresh.token_id,
                token_hash=hash_refresh_token(refresh.raw),
                user_id=user.id,
                family_id=family_id,
                expires_at=refresh.expires_at,
                created_at=now,
            )
        )
        return AuthenticatedSession(
            user=user,
            tokens=SessionTokens(
                access_token=access.raw,
                refresh_token=refresh.raw,
                access_expires_at=access.expires_at,
                refresh_expires_at=refresh.expires_at,
            ),
        )

    def _unlock_if_expired(
        self,
        user: User,
        *,
        now: datetime,
        ip_fingerprint: str,
    ) -> User:
        if user.status != UserStatus.LOCKED or user.locked_until is None or user.locked_until > now:
            return user
        unlocked = replace(
            user,
            status=UserStatus.ACTIVE,
            failed_login_count=0,
            locked_until=None,
            updated_at=now,
            version=user.version + 1,
        )
        saved = self._users.replace_user(unlocked, expected_version=user.version)
        if saved is None:
            refreshed = self._users.get_user(user.id)
            if refreshed is None:
                raise InvalidCredentialsError()
            return refreshed
        self._record_status_audit(
            action="USER_UNLOCKED",
            before=user,
            after=saved,
            ip_fingerprint=ip_fingerprint,
        )
        return saved

    def _record_account_failure(
        self,
        user: User,
        *,
        now: datetime,
        ip_fingerprint: str,
    ) -> bool:
        for _ in range(3):
            failure_count = user.failed_login_count + 1
            is_locked = failure_count >= self._account_max_failures
            failed = replace(
                user,
                status=UserStatus.LOCKED if is_locked else user.status,
                failed_login_count=failure_count,
                locked_until=now + self._lockout if is_locked else None,
                updated_at=now,
                version=user.version + 1,
            )
            saved = self._users.replace_user(failed, expected_version=user.version)
            if saved is not None:
                if is_locked:
                    self._record_status_audit(
                        action="USER_LOCKED",
                        before=user,
                        after=saved,
                        ip_fingerprint=ip_fingerprint,
                    )
                return is_locked
            refreshed = self._users.get_user(user.id)
            if refreshed is None:
                return False
            user = refreshed
        raise UserConcurrentUpdateError()

    def _record_status_audit(
        self,
        *,
        action: str,
        before: User,
        after: User,
        ip_fingerprint: str,
    ) -> None:
        self._audit.record(
            operation_id=str(uuid4()),
            actor_user_id=None,
            action=action,
            resource_type="user",
            resource_id=after.id,
            before={"status": before.status.value},
            after={"status": after.status.value},
            ip_fingerprint=ip_fingerprint,
        )

    def _record_login_success(self, user: User, *, now: datetime) -> User:
        for _ in range(3):
            successful = replace(
                user,
                failed_login_count=0,
                locked_until=None,
                last_login_at=now,
                updated_at=now,
                version=user.version + 1,
            )
            saved = self._users.replace_user(successful, expected_version=user.version)
            if saved is not None:
                return saved
            refreshed = self._users.get_user(user.id)
            if refreshed is None or refreshed.status != UserStatus.ACTIVE:
                raise InvalidCredentialsError()
            user = refreshed
        raise UserConcurrentUpdateError()
