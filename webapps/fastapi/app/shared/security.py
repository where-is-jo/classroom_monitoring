"""비밀번호, JWT, CSRF, 저장용 fingerprint를 다루는 순수 보안 유틸리티."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import jwt
from jwt import InvalidTokenError
from pwdlib import PasswordHash
from pwdlib.exceptions import PwdlibError
from pydantic import SecretStr

_JWT_ALGORITHM = "HS256"
_JWT_ISSUER = "smart-office-monitoring"
_JWT_AUDIENCE = "smart-office-web"
_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class TokenValidationError(ValueError):
    """서명이나 필수 claim이 유효하지 않은 token."""


class TokenExpiredError(TokenValidationError):
    """유효기간이 끝난 token."""


@dataclass(frozen=True)
class IssuedToken:
    raw: str
    token_id: str
    expires_at: datetime


@dataclass(frozen=True)
class AccessTokenClaims:
    user_id: str
    token_id: str
    expires_at: datetime


@dataclass(frozen=True)
class RefreshTokenClaims:
    user_id: str
    token_id: str
    family_id: str
    expires_at: datetime


class PasswordSecurity:
    """pwdlib 권장 Argon2 설정을 사용하는 password hash 경계."""

    def __init__(self) -> None:
        self._password_hash = PasswordHash.recommended()
        self._dummy_hash = self._password_hash.hash(secrets.token_urlsafe(32))

    def hash_password(self, password: str) -> str:
        return self._password_hash.hash(password)

    def verify_password(self, password: str, password_hash: str) -> bool:
        try:
            return self._password_hash.verify(password, password_hash)
        except (PwdlibError, TypeError, ValueError):
            return False

    def verify_dummy(self, password: str) -> None:
        self._password_hash.verify(password, self._dummy_hash)


class TokenSecurity:
    """access/refresh JWT의 발급과 고정 algorithm 검증."""

    def __init__(
        self,
        *,
        access_secret: SecretStr,
        refresh_secret: SecretStr,
        access_ttl_seconds: int,
        refresh_ttl_seconds: int,
    ) -> None:
        self._access_secret = access_secret.get_secret_value()
        self._refresh_secret = refresh_secret.get_secret_value()
        self.access_ttl_seconds = access_ttl_seconds
        self.refresh_ttl_seconds = refresh_ttl_seconds

    def issue_access_token(self, user_id: str, *, now: datetime) -> IssuedToken:
        return self._issue(
            user_id=user_id,
            token_type="access",
            secret=self._access_secret,
            ttl_seconds=self.access_ttl_seconds,
            now=now,
        )

    def issue_refresh_token(
        self,
        user_id: str,
        *,
        family_id: str,
        now: datetime,
    ) -> IssuedToken:
        return self._issue(
            user_id=user_id,
            token_type="refresh",
            secret=self._refresh_secret,
            ttl_seconds=self.refresh_ttl_seconds,
            now=now,
            family_id=family_id,
        )

    def decode_access_token(self, raw_token: str, *, now: datetime) -> AccessTokenClaims:
        payload = self._decode(raw_token, secret=self._access_secret, now=now)
        if payload.get("type") != "access":
            raise TokenValidationError("access token type이 아닙니다.")
        return AccessTokenClaims(
            user_id=str(payload["sub"]),
            token_id=str(payload["jti"]),
            expires_at=_timestamp_to_utc(payload["exp"]),
        )

    def decode_refresh_token(
        self,
        raw_token: str,
        *,
        now: datetime,
    ) -> RefreshTokenClaims:
        payload = self._decode(raw_token, secret=self._refresh_secret, now=now)
        family_id = payload.get("family_id")
        if payload.get("type") != "refresh" or not isinstance(family_id, str):
            raise TokenValidationError("refresh token claim이 올바르지 않습니다.")
        return RefreshTokenClaims(
            user_id=str(payload["sub"]),
            token_id=str(payload["jti"]),
            family_id=family_id,
            expires_at=_timestamp_to_utc(payload["exp"]),
        )

    def _issue(
        self,
        *,
        user_id: str,
        token_type: str,
        secret: str,
        ttl_seconds: int,
        now: datetime,
        family_id: str | None = None,
    ) -> IssuedToken:
        token_id = str(uuid4())
        expires_at = now + timedelta(seconds=ttl_seconds)
        payload: dict[str, Any] = {
            "sub": user_id,
            "jti": token_id,
            "type": token_type,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
            "iss": _JWT_ISSUER,
            "aud": _JWT_AUDIENCE,
        }
        if family_id is not None:
            payload["family_id"] = family_id
        raw_token = jwt.encode(payload, secret, algorithm=_JWT_ALGORITHM)
        return IssuedToken(raw=raw_token, token_id=token_id, expires_at=expires_at)

    @staticmethod
    def _decode(raw_token: str, *, secret: str, now: datetime) -> dict[str, Any]:
        try:
            payload = jwt.decode(
                raw_token,
                secret,
                algorithms=[_JWT_ALGORITHM],
                issuer=_JWT_ISSUER,
                audience=_JWT_AUDIENCE,
                options={
                    "verify_exp": False,
                    "verify_iat": False,
                    "require": ["sub", "jti", "type", "iat", "exp", "iss", "aud"],
                },
            )
        except InvalidTokenError:
            raise TokenValidationError("token을 검증할 수 없습니다.") from None
        expires_at = _timestamp_to_utc(payload.get("exp"))
        if expires_at <= now:
            raise TokenExpiredError("token이 만료됐습니다.")
        if not isinstance(payload.get("sub"), str) or not isinstance(
            payload.get("jti"), str
        ):
            raise TokenValidationError("token subject가 올바르지 않습니다.")
        return payload


def canonicalize_email(email: str) -> str:
    return email.strip().lower()


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_PATTERN.fullmatch(email)) and len(email) <= 254


def validate_password_policy(password: str, *, minimum_length: int) -> tuple[str, ...]:
    violations: list[str] = []
    if len(password) < minimum_length:
        violations.append(f"{minimum_length}자 이상")
    if not any(character.islower() for character in password):
        violations.append("소문자 포함")
    if not any(character.isupper() for character in password):
        violations.append("대문자 포함")
    if not any(character.isdigit() for character in password):
        violations.append("숫자 포함")
    if not any(not character.isalnum() for character in password):
        violations.append("기호 포함")
    return tuple(violations)


def hash_refresh_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def issue_csrf_token(secret: SecretStr) -> str:
    nonce = secrets.token_urlsafe(32)
    signature = hmac.new(
        secret.get_secret_value().encode("utf-8"),
        nonce.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{nonce}.{signature}"


def verify_csrf_token(token: str, secret: SecretStr) -> bool:
    try:
        nonce, provided_signature = token.rsplit(".", 1)
    except ValueError:
        return False
    expected_signature = hmac.new(
        secret.get_secret_value().encode("utf-8"),
        nonce.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(provided_signature, expected_signature)


def fingerprint_ip(ip_address: str, secret: SecretStr) -> str:
    return hmac.new(
        secret.get_secret_value().encode("utf-8"),
        ip_address.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _timestamp_to_utc(value: object) -> datetime:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TokenValidationError("token time claim이 올바르지 않습니다.")
    return datetime.fromtimestamp(value, tz=UTC)
