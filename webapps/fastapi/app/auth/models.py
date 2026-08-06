"""인증과 refresh rotation 도메인 값."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from ..users.models import User


@dataclass(frozen=True)
class RefreshToken:
    id: str
    token_hash: str
    user_id: str
    family_id: str
    expires_at: datetime
    created_at: datetime
    revoked_at: datetime | None = None
    replaced_by_id: str | None = None


class RefreshRotationStatus(StrEnum):
    ROTATED = "ROTATED"
    INVALID = "INVALID"
    REUSED = "REUSED"


@dataclass(frozen=True)
class RefreshRotationResult:
    status: RefreshRotationStatus
    current: RefreshToken | None = None


@dataclass(frozen=True)
class SessionTokens:
    access_token: str
    refresh_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime


@dataclass(frozen=True)
class AuthenticatedSession:
    user: User
    tokens: SessionTokens


@dataclass(frozen=True)
class LoginCommand:
    email: str
    password: str
    ip_fingerprint: str
