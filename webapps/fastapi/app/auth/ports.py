"""refresh token 저장소 외부 I/O 포트."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .models import RefreshRotationResult, RefreshToken


class AuthRepository(Protocol):
    def create_refresh_token(self, refresh_token: RefreshToken) -> RefreshToken: ...

    def get_refresh_token(self, token_hash: str) -> RefreshToken | None: ...

    def rotate_refresh_token(
        self,
        *,
        current_token_hash: str,
        replacement: RefreshToken,
        now: datetime,
    ) -> RefreshRotationResult: ...

    def revoke_family(self, family_id: str, *, now: datetime) -> None: ...

    def revoke_user_tokens(self, user_id: str, *, now: datetime) -> None: ...
