"""인증 HTTP 요청과 응답 스키마."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from ..users.models import User
from ..users.schemas import UserResponse


class LoginRequest(BaseModel):
    email: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class LoginForm(LoginRequest):
    next: str = Field(default="/events", max_length=500)


class SessionResponse(BaseModel):
    user: UserResponse
    access_expires_at: datetime
    refresh_expires_at: datetime


class MeResponse(BaseModel):
    user: UserResponse

    @classmethod
    def from_user(cls, user: User) -> MeResponse:
        return cls(user=UserResponse.from_user(user))


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)
    operation_id: UUID = Field(default_factory=uuid4)
