"""입구 얼굴 관측 이벤트의 검증·저장·조회 서비스."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from ..shared.broadcaster import InMemoryBroadcaster
from ..shared.student_identity import StudentLookupPort
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

logger = logging.getLogger(__name__)


class EntryIdentityEventService:
    def __init__(
        self,
        repository: EntryIdentityEventRepository,
        stream_repository: VideoStreamRepository,
        broadcaster: InMemoryBroadcaster,
        student_lookup: StudentLookupPort,
        *,
        retention_days: int,
        page_size_max: int,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._stream_repository = stream_repository
        self._broadcaster = broadcaster
        self._student_lookup = student_lookup
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
        # 저장 뒤 상태 갱신이 실패해 worker가 재전송해도 복구할 수 있도록 duplicate에서도
        # 이 멱등 갱신을 수행한다. 저장소는 더 오래된 시각으로 되돌아가지 않아야 한다.
        self._stream_repository.update_last_detection(saved.camera_id, saved.captured_at)
        if created:
            self._broadcaster.publish(self._to_realtime_event(saved))
        return EntryIdentityEventSaveResult(saved, created)

    def publish_overlay(
        self,
        *,
        event_id: str,
        camera_id: str,
        captured_at: datetime,
        sequence: int,
        frame: EntryFrameInfo,
        processing_status: EntryIdentityProcessingStatus,
        observations: tuple[EntryFaceObservation, ...],
    ) -> None:
        """얼굴 상자만 구독자에게 내보낸다. **저장하지 않는다.**

        화면에 상자를 그리는 일과 관측을 남기는 일은 요구가 반대다. 오버레이는 자주
        와야 하고 놓쳐도 다음 프레임이 덮어 그리지만, 저장은 그 이벤트가 유일한
        기회이고 만료 정책까지 걸려 있다. 같은 경로에 두면 저장 주기가 곧 화면 갱신
        주기가 된다 — CCTV에서 같은 문제를 겪고 갈랐다(결정 0047).

        `save_event`와 달리 강의실·stream 확인도 하지 않는다. 구독자는 `camera_id`로만
        걸러 받으므로(`stream_entry_identity_events`) 등록되지 않은 카메라의 payload는
        아무에게도 닿지 않으며, 확인하려면 저장소 왕복이 생겨 이 경로를 만든 이유가
        없어진다.
        """
        received_at = self._aware_utc(self._clock())
        event = EntryIdentityEvent(
            event_id=event_id,
            camera_id=camera_id,
            # 저장하지 않으므로 stream_id·expires_at은 화면에 쓰이지 않는다.
            # `_to_realtime_event`가 두 값을 쓰지 않는 것을 테스트가 고정한다.
            stream_id="",
            captured_at=self._aware_utc(captured_at),
            sequence=sequence,
            frame=frame,
            processing_status=processing_status,
            observations=observations,
            received_at=received_at,
            expires_at=received_at,
        )
        self._broadcaster.publish(self._to_realtime_event(event))

    def resolve_realtime_camera_id(self, stream_id: str) -> str:
        """얼굴 SSE를 열 수 있는 활성 입구 stream인지 확인하고 camera ID를 반환한다."""
        stream = self._stream_repository.find_by_id(
            stream_id
        ) or self._stream_repository.find_by_camera_id(stream_id)
        if stream is None:
            raise VideoStreamNotFoundError()
        self._require_identity_only_stream(stream.role, enabled=stream.enabled)
        return stream.camera_id

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

    def _to_realtime_event(self, event: EntryIdentityEvent) -> dict[str, object]:
        """저장 계약에서 생체 원본·내부 ID를 빼고 화면용 얼굴 관측만 만든다."""
        observations = [
            {
                "face_track_id": observation.face_track_id,
                "face_bbox": list(observation.face_bbox),
                "detection_confidence": observation.detection_confidence,
                "identity_status": observation.identity_status.value,
                "display_label": self._display_label(observation),
            }
            for observation in event.observations
        ]
        return {
            "type": "entry-identity",
            "event_id": event.event_id,
            "camera_id": event.camera_id,
            "captured_at": event.captured_at.isoformat(),
            "sequence": event.sequence,
            "frame": {
                "width_pixels": event.frame.width_pixels,
                "height_pixels": event.frame.height_pixels,
            },
            "processing_status": event.processing_status.value,
            "observations": observations,
            "observations_count": len(observations),
        }

    def _display_label(self, observation: EntryFaceObservation) -> str:
        if observation.identity_status is EntryIdentityStatus.UNKNOWN:
            return "미등록 얼굴"
        if observation.identity_status is EntryIdentityStatus.UNCERTAIN:
            return "판정 보류"
        if observation.student_id is None:
            return "등록 얼굴"
        try:
            student = self._student_lookup.find_by_id(observation.student_id)
        except Exception:
            # 학생 이름·번호·내부 ID는 로그에 남기지 않는다.
            logger.warning("입구 얼굴 실시간 라벨 보강 중 학생 조회에 실패했습니다.")
            return "등록 얼굴"
        return student.name if student is not None and student.is_active else "등록 얼굴"
