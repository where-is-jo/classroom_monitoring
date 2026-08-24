"""신원 인계 route HTTP 스키마."""

from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel, Field

from .models import (
    HandoverReferenceImage,
    HandoverZone,
    IdentityHandoverRoute,
    SaveIdentityHandoverRouteCommand,
)


class HandoverZoneSchema(BaseModel):
    left: float = Field(ge=0, le=1)
    top: float = Field(ge=0, le=1)
    right: float = Field(ge=0, le=1)
    bottom: float = Field(ge=0, le=1)

    @classmethod
    def from_domain(cls, zone: HandoverZone) -> HandoverZoneSchema:
        return cls(
            left=zone.left,
            top=zone.top,
            right=zone.right,
            bottom=zone.bottom,
        )


class SaveIdentityHandoverRouteRequest(BaseModel):
    entry_camera_id: str = Field(min_length=1, max_length=128)
    classroom_entry_zone: HandoverZoneSchema
    reference_image_revision: int = Field(ge=1)

    def to_command(
        self, classroom_id: str, classroom_camera_id: str
    ) -> SaveIdentityHandoverRouteCommand:
        zone = self.classroom_entry_zone
        return SaveIdentityHandoverRouteCommand(
            classroom_id=classroom_id,
            entry_camera_id=self.entry_camera_id,
            classroom_camera_id=classroom_camera_id,
            classroom_entry_zone=HandoverZone(zone.left, zone.top, zone.right, zone.bottom),
            reference_image_revision=self.reference_image_revision,
        )


class HandoverReferenceImageResponse(BaseModel):
    classroom_id: str
    camera_id: str
    display_name: str
    revision: int
    image_url: str

    @classmethod
    def from_domain(cls, image: HandoverReferenceImage) -> HandoverReferenceImageResponse:
        return cls(
            classroom_id=image.classroom_id,
            camera_id=image.camera_id,
            display_name=image.display_name,
            revision=image.revision,
            image_url=(
                f"/api/v1/classrooms/{image.classroom_id}/identity-handover-reference-image"
                f"?camera_id={image.camera_id}"
            ),
        )


class IdentityHandoverRouteResponse(BaseModel):
    classroom_id: str
    entry_camera_id: str
    classroom_camera_id: str
    classroom_entry_zone: HandoverZoneSchema
    reference_image_revision: int
    updated_at: datetime
    worker_environment_value: str

    @classmethod
    def from_domain(cls, route: IdentityHandoverRoute) -> IdentityHandoverRouteResponse:
        worker_value = json.dumps(
            [
                {
                    "entry_camera_id": route.entry_camera_id,
                    "classroom_camera_id": route.classroom_camera_id,
                    "classroom_entry_zone": list(route.classroom_entry_zone.as_tuple()),
                }
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return cls(
            classroom_id=route.classroom_id,
            entry_camera_id=route.entry_camera_id,
            classroom_camera_id=route.classroom_camera_id,
            classroom_entry_zone=HandoverZoneSchema.from_domain(route.classroom_entry_zone),
            reference_image_revision=route.reference_image_revision,
            updated_at=route.updated_at,
            worker_environment_value=f"IDENTITY_HANDOVER_ROUTES={worker_value}",
        )


class IdentityHandoverRouteListResponse(BaseModel):
    items: list[IdentityHandoverRouteResponse]


class WorkerIdentityHandoverRouteResponse(BaseModel):
    entry_camera_id: str
    classroom_camera_id: str
    classroom_entry_zone: list[float] = Field(min_length=4, max_length=4)

    @classmethod
    def from_domain(cls, route: IdentityHandoverRoute) -> WorkerIdentityHandoverRouteResponse:
        return cls(
            entry_camera_id=route.entry_camera_id,
            classroom_camera_id=route.classroom_camera_id,
            classroom_entry_zone=list(route.classroom_entry_zone.as_tuple()),
        )


class WorkerIdentityHandoverRouteListResponse(BaseModel):
    items: list[WorkerIdentityHandoverRouteResponse]
