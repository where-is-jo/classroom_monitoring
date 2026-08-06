"""알림 HTTP 요청·응답 스키마."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from .models import MockDeliveryStatus, NotificationDataValue


class NotificationResponse(BaseModel):
    id: str
    type: str
    title: str
    body: str
    data: dict[str, NotificationDataValue]
    target_route: str | None
    is_read: bool
    read_at: datetime | None
    created_at: datetime


class NotificationListResponse(BaseModel):
    items: list[NotificationResponse]
    total: int
    limit: int
    offset: int


class NotificationUnreadCountResponse(BaseModel):
    unread_count: int = Field(ge=0)


class NotificationReadRequest(BaseModel):
    operation_id: UUID


class NotificationReadBatchRequest(BaseModel):
    operation_id: UUID


class NotificationReadBatchResponse(BaseModel):
    updated_count: int = Field(ge=0)


class MockDeliveryResponse(BaseModel):
    id: str
    notification_id: str
    provider: str
    status: MockDeliveryStatus
    attempt: int = Field(ge=1)
    request_payload: dict[str, NotificationDataValue]
    result_payload: dict[str, NotificationDataValue]
    error: str | None
    attempted_at: datetime


class MockDeliveryListResponse(BaseModel):
    items: list[MockDeliveryResponse]
    total: int
    limit: int
    offset: int


class MockDeliveryAttemptRequest(BaseModel):
    notification_id: str = Field(min_length=1, max_length=128)
    operation_id: UUID
