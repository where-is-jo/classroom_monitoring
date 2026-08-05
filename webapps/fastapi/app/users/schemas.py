"""사용자 HTTP 요청과 응답 스키마."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

from .models import User, UserPage, UserRole, UserStatus


class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    role: UserRole
    status: UserStatus
    locked_until: datetime | None
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime
    version: int

    @classmethod
    def from_user(cls, user: User) -> "UserResponse":
        return cls(
            id=user.id,
            email=user.email,
            name=user.name,
            role=user.role,
            status=user.status,
            locked_until=user.locked_until,
            last_login_at=user.last_login_at,
            created_at=user.created_at,
            updated_at=user.updated_at,
            version=user.version,
        )


class UserListResponse(BaseModel):
    items: list[UserResponse]
    total: int
    limit: int
    offset: int

    @classmethod
    def from_page(
        cls,
        page: UserPage,
        *,
        limit: int,
        offset: int,
    ) -> "UserListResponse":
        return cls(
            items=[UserResponse.from_user(user) for user in page.items],
            total=page.total,
            limit=limit,
            offset=offset,
        )


class CreateUserRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=100)
    role: UserRole
    operation_id: UUID = Field(default_factory=uuid4)


class UpdateUserRequest(BaseModel):
    expected_version: int = Field(ge=0)
    operation_id: UUID = Field(default_factory=uuid4)
    email: str | None = Field(default=None, min_length=3, max_length=254)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    role: UserRole | None = None
    status: UserStatus | None = None

    @model_validator(mode="after")
    def _at_least_one_change(self) -> "UpdateUserRequest":
        if all(
            value is None
            for value in (self.email, self.name, self.role, self.status)
        ):
            raise ValueError("변경할 필드가 하나 이상 필요합니다.")
        return self


class DeactivateUserRequest(BaseModel):
    operation_id: UUID = Field(default_factory=uuid4)


class CreateUserForm(CreateUserRequest):
    csrf_token: str = Field(min_length=1)


class UpdateUserForm(UpdateUserRequest):
    csrf_token: str = Field(min_length=1)


class DeactivateUserForm(DeactivateUserRequest):
    csrf_token: str = Field(min_length=1)
