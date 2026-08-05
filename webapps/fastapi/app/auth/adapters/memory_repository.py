"""refresh token rotation을 직렬화하는 memory 저장소."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from threading import RLock

from ..models import (
    RefreshRotationResult,
    RefreshRotationStatus,
    RefreshToken,
)


class InMemoryAuthRepository:
    def __init__(self) -> None:
        self._tokens_by_hash: dict[str, RefreshToken] = {}
        self._lock = RLock()

    def create_refresh_token(self, refresh_token: RefreshToken) -> RefreshToken:
        with self._lock:
            existing = self._tokens_by_hash.get(refresh_token.token_hash)
            if existing is not None:
                return existing
            self._tokens_by_hash[refresh_token.token_hash] = refresh_token
            return refresh_token

    def get_refresh_token(self, token_hash: str) -> RefreshToken | None:
        with self._lock:
            return self._tokens_by_hash.get(token_hash)

    def rotate_refresh_token(
        self,
        *,
        current_token_hash: str,
        replacement: RefreshToken,
        now: datetime,
    ) -> RefreshRotationResult:
        with self._lock:
            current = self._tokens_by_hash.get(current_token_hash)
            if current is None or current.expires_at <= now:
                return RefreshRotationResult(RefreshRotationStatus.INVALID, current)
            if current.revoked_at is not None:
                if current.replaced_by_id is not None:
                    self.revoke_family(current.family_id, now=now)
                    return RefreshRotationResult(RefreshRotationStatus.REUSED, current)
                return RefreshRotationResult(RefreshRotationStatus.INVALID, current)

            self._tokens_by_hash[replacement.token_hash] = replacement
            rotated = replace(
                current,
                revoked_at=now,
                replaced_by_id=replacement.id,
            )
            self._tokens_by_hash[current_token_hash] = rotated
            return RefreshRotationResult(RefreshRotationStatus.ROTATED, rotated)

    def revoke_family(self, family_id: str, *, now: datetime) -> None:
        with self._lock:
            for token_hash, token in list(self._tokens_by_hash.items()):
                if token.family_id == family_id and token.revoked_at is None:
                    self._tokens_by_hash[token_hash] = replace(token, revoked_at=now)

    def revoke_user_tokens(self, user_id: str, *, now: datetime) -> None:
        with self._lock:
            for token_hash, token in list(self._tokens_by_hash.items()):
                if token.user_id == user_id and token.revoked_at is None:
                    self._tokens_by_hash[token_hash] = replace(token, revoked_at=now)
