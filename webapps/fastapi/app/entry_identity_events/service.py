"""입구 얼굴 관측 이벤트의 검증·저장·조회 서비스."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from ..video_monitoring.errors import VideoStreamNotFoundError
from ..video_monitoring.models import CameraRole
from ..video_monitoring.ports import VideoStreamRepository
from .errors import EntryIdentityCameraRoleError, EntryIdentityQueryError
from .models import (
    EntryFaceObservation,
    EntryFrameInfo,
    EntryIdentityEvent,
    EntryIdentityEventPage,
    EntryIdentityEventSaveResult,
    EntryIdentityProcessingStatus,
    EntryIdentityStatus,
)
from .ports import EntryIdentityEventRepository


class EntryIdentityEventService:
    def __init__(
        self,
        repository: EntryIdentityEventRepository,
        stream_repository: VideoStreamRepository,
        *,
        retention_days: int,
        page_size_max: int,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._stream_repository = stream_repository
        self._retention_days = retention_days
        self._page_size_max = page_size_max
        self._clock = clock

    def save_event(
        self,
        *,
        event_id: str,
        camera_id: str,
        captured_at: datetime,
        sequence: int,
        frame: EntryFrameInfo,
        processing_status: EntryIdentityProcessingStatus,
        observations: tuple[EntryFaceObservation, ...],
    ) -> EntryIdentityEventSaveResult:
        captured_at = self._aware_utc(captured_at)
        expected_event_id = (
            f"{camera_id}-{int(captured_at.timestamp() * 1000)}-{sequence}-entry-face"
        )
        if event_id != expected_event_id:
            raise EntryIdentityQueryError("이벤트 ID가 카메라·촬영 시각·sequence와 다릅니다.")
        stream = self._stream_repository.find_by_camera_id(camera_id)
        if stream is None:
            raise VideoStreamNotFoundError()
        self._require_identity_only_stream(stream.role, enabled=stream.enabled)

        received_at = self._aware_utc(self._clock())
        event = EntryIdentityEvent(
            event_id=event_id,
            camera_id=camera_id,
            stream_id=stream.id,
            captured_at=captured_at,
            sequence=sequence,
            frame=frame,
            processing_status=processing_status,
            observations=observations,
            received_at=received_at,
            expires_at=received_at + timedelta(days=self._retention_days),
        )
        saved, created = self._repository.save(event)
        return EntryIdentityEventSaveResult(saved, created)

    def list_events(
        self,
        stream_id: str,
        *,
        status: EntryIdentityStatus | None,
        student_id: str | None,
        from_at: datetime | None,
        to_at: datetime | None,
        limit: int,
        cursor: str | None,
    ) -> EntryIdentityEventPage:
        stream = self._stream_repository.find_by_id(stream_id)
        if stream is None:
            raise VideoStreamNotFoundError()
        self._require_identity_only_stream(stream.role, enabled=stream.enabled)
        if not 1 <= limit <= self._page_size_max:
            raise EntryIdentityQueryError(f"조회 개수는 1~{self._page_size_max} 사이여야 합니다.")
        from_at = None if from_at is None else self._aware_utc(from_at)
        to_at = None if to_at is None else self._aware_utc(to_at)
        if from_at is not None and to_at is not None and from_at > to_at:
            raise EntryIdentityQueryError("조회 시작 시각은 종료 시각보다 늦을 수 없습니다.")
        normalized_student_id = None
        if student_id is not None:
            normalized_student_id = student_id.strip()
            if not normalized_student_id:
                raise EntryIdentityQueryError("student_id는 비어 있을 수 없습니다.")
        return self._repository.list_by_stream(
            stream_id,
            status=status,
            student_id=normalized_student_id,
            from_at=from_at,
            to_at=to_at,
            limit=limit,
            cursor=cursor,
        )

    @staticmethod
    def _aware_utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise EntryIdentityQueryError("시각에는 timezone이 필요합니다.")
        return value.astimezone(UTC)

    @staticmethod
    def _require_identity_only_stream(role: CameraRole, *, enabled: bool) -> None:
        if not enabled or role is not CameraRole.IDENTITY_ONLY:
            raise EntryIdentityCameraRoleError()
