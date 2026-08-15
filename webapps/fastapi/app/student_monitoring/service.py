"""Student monitoring service."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from ..classrooms.errors import ClassroomNotFoundError
from ..classrooms.mapping import map_detections_to_observations
from ..classrooms.models import Seat, SeatAssignment, SeatObservation
from ..classrooms.service import ClassroomService
from ..roi_connections.errors import RoiConnectionNotFoundError
from ..roi_connections.mapping import RoiMappingReason, map_bbox_to_roi
from ..roi_connections.models import RoiConnection
from ..roi_connections.service import RoiConnectionService
from ..shared.broadcaster import InMemoryBroadcaster
from ..shared.student_identity import StudentIdentity, StudentLookupPort
from ..video_monitoring.ports import VideoStreamRepository
from .errors import VideoStreamNotFoundError
from .models import (
    Detection,
    DetectionEvent,
    StudentSeatState,
    StudentState,
    VideoSegment,
)
from .ports import DetectionEventRepository, VideoSegmentRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InferenceEventResult:
    """Result of receiving an inference event."""

    event: DetectionEvent
    is_new: bool


@dataclass(frozen=True)
class _IdentifiedDetection:
    event: DetectionEvent
    detection: Detection


class StudentMonitoringService:
    """Student monitoring service."""

    def __init__(
        self,
        detection_repository: DetectionEventRepository,
        segment_repository: VideoSegmentRepository,
        stream_repository: VideoStreamRepository,
        broadcaster: InMemoryBroadcaster,
        classroom_service: ClassroomService,
        roi_service: RoiConnectionService,
        *,
        occupancy_confidence_threshold: float,
        identity_confidence_threshold: float,
        stale_seconds: int,
        recent_event_limit: int,
        clock: Callable[[], datetime],
        student_lookup: StudentLookupPort,
    ) -> None:
        self._detection_repository = detection_repository
        self._segment_repository = segment_repository
        self._stream_repository = stream_repository
        self._broadcaster = broadcaster
        self._classroom_service = classroom_service
        self._roi_service = roi_service
        self._confidence_threshold = occupancy_confidence_threshold
        self._identity_confidence_threshold = identity_confidence_threshold
        self._stale_seconds = stale_seconds
        self._recent_event_limit = recent_event_limit
        self._clock = clock
        self._student_lookup = student_lookup

    def receive_inference_event(self, event: DetectionEvent) -> InferenceEventResult:
        """Receive inference event."""
        # Check camera exists
        stream = self._stream_repository.find_by_camera_id(event.camera_id)
        if stream is None:
            raise VideoStreamNotFoundError()

        resolved_event = replace(
            event,
            stream_id=stream.id,
            classroom_id=stream.classroom_id,
        )

        # Check if event already exists
        existing = self._detection_repository.find_by_event_id(event.event_id)
        is_new = existing is None

        # Save event (idempotent)
        saved_event = self._detection_repository.save(resolved_event)

        # Update last detection timestamp only for new events
        if is_new:
            self._stream_repository.update_last_detection(
                saved_event.camera_id, saved_event.captured_at
            )

            # Publish to SSE only for new events
            # frame·detections를 함께 보내야 브라우저가 bbox overlay를 그릴 수 있다.
            self._broadcaster.publish(
                {
                    "type": "detection",
                    "event_id": saved_event.event_id,
                    "camera_id": saved_event.camera_id,
                    "captured_at": saved_event.captured_at.isoformat(),
                    "sequence": saved_event.sequence,
                    "frame": {
                        "width_pixels": saved_event.frame.width_pixels,
                        "height_pixels": saved_event.frame.height_pixels,
                    },
                    "detections": [
                        {
                            "detection_id": d.detection_id,
                            "class_id": d.class_id,
                            "class_name": d.class_name,
                            "confidence": d.confidence,
                            "bbox": list(d.bbox),
                            "student_id": d.student_id,
                            "display_label": self._safe_detection_label(d),
                        }
                        for d in saved_event.detections
                    ],
                    "detections_count": len(saved_event.detections),
                }
            )

            # 탐지→좌석 매핑: 새 이벤트일 때만 좌석 관측 batch를 만들고,
            # 반영된 좌석 상태를 occupancy SSE로 발행한다.
            # 같은 event_id를 다시 받으면 batch 멱등 처리와 어긋나는 관측이 생길 수 없도록
            # 재수신에서는 매핑을 반복하지 않는다.
            # 매핑·batch 기록·SSE 발행이 실패해도 탐지 이벤트 저장 결과는 그대로 돌려준다.
            if saved_event.detections and saved_event.classroom_id:
                classroom_id = saved_event.classroom_id
                try:
                    seats = self._classroom_service.list_all_seats(classroom_id)
                    observations = map_detections_to_observations(
                        saved_event.detections,
                        seats,
                        saved_event.frame,
                        self._confidence_threshold,
                    )
                    if observations:
                        self._classroom_service.record_seat_observation_batch(
                            event_id=saved_event.event_id,
                            classroom_id=classroom_id,
                            observations=observations,
                            observed_at=saved_event.captured_at,
                        )
                        self._publish_occupancy_events(
                            event_id=saved_event.event_id,
                            classroom_id=classroom_id,
                            observations=observations,
                        )
                except ClassroomNotFoundError:
                    logger.warning(
                        "event_id=%s camera_id=%s classroom_id=%s "
                        "활성 강의실 참조가 없어 좌석 매핑을 건너뜁니다.",
                        saved_event.event_id,
                        saved_event.camera_id,
                        classroom_id,
                    )
                except Exception:
                    logger.exception(
                        "event_id=%s camera_id=%s classroom_id=%s "
                        "좌석 매핑 중 오류가 발생해 건너뜁니다.",
                        saved_event.event_id,
                        saved_event.camera_id,
                        classroom_id,
                    )

            if saved_event.classroom_id:
                try:
                    self._publish_student_state_events(saved_event)
                except Exception:
                    logger.exception(
                        "event_id=%s camera_id=%s classroom_id=%s "
                        "학생 상태 SSE 계산·발행 중 오류가 발생해 건너뜁니다.",
                        saved_event.event_id,
                        saved_event.camera_id,
                        saved_event.classroom_id,
                    )

        return InferenceEventResult(event=saved_event, is_new=is_new)

    def _safe_detection_label(self, detection: Detection) -> str:
        """활성 학생의 임계값 이상 식별만 이름으로 표시한다."""
        if (
            detection.class_name.casefold() != "person"
            or detection.confidence < self._confidence_threshold
            or detection.student_id is None
            or detection.identity_confidence is None
            or detection.identity_confidence < self._identity_confidence_threshold
        ):
            return "사람"
        try:
            student = self._student_lookup.find_by_id(detection.student_id)
        except Exception:
            logger.exception(
                "student_id=%s 탐지 라벨 보강 중 학생 조회에 실패했습니다.",
                detection.student_id,
            )
            return "사람"
        return student.name if student is not None and student.is_active else "사람"

    def _publish_student_state_events(self, event: DetectionEvent) -> None:
        """신규 이벤트에서 식별 후보가 있었던 학생의 최신 상태를 발행한다."""
        target_student_ids = {
            detection.student_id
            for detection in event.detections
            if detection.student_id is not None
        }
        if not target_student_ids:
            return
        for state in self.list_student_states(event.classroom_id):
            if state.student_id not in target_student_ids:
                continue
            self._broadcaster.publish(
                {
                    "type": "student-state",
                    "event_id": event.event_id,
                    "classroom_id": event.classroom_id,
                    "student_id": state.student_id,
                    "student_name": state.student_name,
                    "student_no": state.student_no,
                    "assigned_seat_id": state.assigned_seat_id,
                    "assigned_seat_label": state.assigned_seat_label,
                    "current_seat_id": state.current_seat_id,
                    "current_state": state.current_state.value,
                    "confidence": state.confidence,
                    "observed_at": (
                        state.last_observed_at.isoformat()
                        if state.last_observed_at is not None
                        else None
                    ),
                }
            )

    def _publish_occupancy_events(
        self,
        *,
        event_id: str,
        classroom_id: str,
        observations: tuple[SeatObservation, ...],
    ) -> None:
        """좌석 관측 batch 반영 뒤 실제 좌석 상태를 occupancy SSE로 발행한다.

        관측값을 그대로 쓰지 않고 저장소에 반영된 current_occupancy를 기준으로 발행해,
        오래된 관측이 적용되지 않은 좌석도 화면에는 실제 상태가 전달되게 한다.
        """
        seats = self._classroom_service.list_all_seats(classroom_id)
        seats_by_id = {seat.id: seat for seat in seats}
        for observation in observations:
            seat = seats_by_id.get(observation.seat_id)
            if seat is None:
                continue
            occupancy = seat.current_occupancy
            self._broadcaster.publish(
                {
                    "type": "occupancy",
                    "event_id": event_id,
                    "classroom_id": classroom_id,
                    "seat_id": seat.id,
                    "state": occupancy.state.value,
                    "confidence": occupancy.confidence,
                    "observed_at": (
                        occupancy.observed_at.isoformat()
                        if occupancy.observed_at is not None
                        else None
                    ),
                }
            )

    def receive_video_segment(self, segment: VideoSegment) -> VideoSegment:
        """Receive video segment."""
        # Check camera exists
        stream = self._stream_repository.find_by_camera_id(segment.camera_id)
        if stream is None:
            raise VideoStreamNotFoundError()

        # Save segment (idempotent)
        return self._segment_repository.save(segment)

    def list_student_states(self, classroom_id: str) -> list[StudentSeatState]:
        """최근 식별과 카메라 ROI를 좌석 지정에 대조한 읽기 모델을 반환한다."""
        seats = self._classroom_service.list_all_seats(classroom_id)
        assignments = self._classroom_service.list_assignments_raw(classroom_id)
        active_assignments = self._active_assignments(classroom_id, seats, assignments)
        if not active_assignments:
            return []

        since = self._clock() - timedelta(seconds=self._stale_seconds)
        events = self._detection_repository.find_recent_by_classroom(
            classroom_id,
            since,
            limit=self._recent_event_limit,
        )
        identified = self._select_recent_identifications(
            events,
            {student.id for _, _, student in active_assignments},
            classroom_id=classroom_id,
            since=since,
        )
        connections_by_camera: dict[str, list[RoiConnection]] = {}
        result: list[StudentSeatState] = []

        for assignment, seat, student in active_assignments:
            selected = identified.get(student.id)
            current_seat_id: str | None = None
            state = StudentState.UNKNOWN
            confidence: float | None = None
            observed_at: datetime | None = None
            if selected is not None:
                event = selected.event
                detection = selected.detection
                confidence = detection.identity_confidence
                observed_at = event.captured_at
                connections = connections_by_camera.get(event.camera_id)
                if connections is None:
                    try:
                        connections = self._roi_service.list_valid_connections(
                            classroom_id, event.camera_id
                        )
                    except RoiConnectionNotFoundError:
                        logger.warning(
                            "classroom_id=%s camera_id=%s 유효한 카메라 참조가 없어 "
                            "학생 상태 ROI 매핑을 건너뜁니다.",
                            classroom_id,
                            event.camera_id,
                        )
                        connections = []
                    connections_by_camera[event.camera_id] = connections

                mapping = map_bbox_to_roi(
                    detection.bbox,
                    frame_width_pixels=event.frame.width_pixels,
                    frame_height_pixels=event.frame.height_pixels,
                    connections=connections,
                )
                if mapping.reason == RoiMappingReason.AMBIGUOUS:
                    logger.warning(
                        "event_id=%s camera_id=%s detection_id=%s student_id=%s "
                        "겹치는 좌석 ROI로 상태를 판정할 수 없습니다.",
                        event.event_id,
                        event.camera_id,
                        detection.detection_id,
                        student.id,
                    )
                if mapping.connection is not None:
                    current_seat_id = mapping.connection.seat_id
                    state = (
                        StudentState.PRESENT
                        if current_seat_id == assignment.seat_id
                        else StudentState.WRONG_SEAT
                    )

            result.append(
                StudentSeatState(
                    student_id=student.id,
                    student_name=student.name,
                    student_no=student.student_no,
                    assigned_seat_id=assignment.seat_id,
                    assigned_seat_label=seat.label,
                    current_seat_id=current_seat_id,
                    current_state=state,
                    confidence=confidence,
                    last_observed_at=observed_at,
                )
            )
        return result

    def _active_assignments(
        self,
        classroom_id: str,
        seats: list[Seat],
        assignments: list[SeatAssignment],
    ) -> list[tuple[SeatAssignment, Seat, StudentIdentity]]:
        seats_by_id = {seat.id: seat for seat in seats if seat.is_active}
        candidates: list[tuple[SeatAssignment, Seat, StudentIdentity]] = []
        for assignment in assignments:
            seat = seats_by_id.get(assignment.seat_id)
            student = self._student_lookup.find_by_id(assignment.student_id)
            if (
                assignment.classroom_id != classroom_id
                or seat is None
                or student is None
                or not student.is_active
            ):
                continue
            candidates.append((assignment, seat, student))
        candidates.sort(
            key=lambda item: (
                item[1].code,
                item[1].id,
                item[2].student_no,
                item[2].id,
            )
        )

        result: list[tuple[SeatAssignment, Seat, StudentIdentity]] = []
        seen_students: set[str] = set()
        for candidate in candidates:
            student_id = candidate[2].id
            if student_id in seen_students:
                logger.warning(
                    "classroom_id=%s student_id=%s 중복 좌석 지정이 있어 후속 지정을 제외합니다.",
                    classroom_id,
                    student_id,
                )
                continue
            seen_students.add(student_id)
            result.append(candidate)
        return result

    def _select_recent_identifications(
        self,
        events: list[DetectionEvent],
        student_ids: set[str],
        *,
        classroom_id: str,
        since: datetime,
    ) -> dict[str, _IdentifiedDetection]:
        eligible_events = [
            event
            for event in events
            if event.classroom_id == classroom_id and event.captured_at >= since
        ]
        eligible_events.sort(key=lambda event: event.event_id)
        eligible_events.sort(key=lambda event: event.captured_at, reverse=True)
        selected: dict[str, _IdentifiedDetection] = {}
        for event in eligible_events:
            detections = [
                detection
                for detection in event.detections
                if detection.student_id in student_ids
                and detection.class_name.casefold() == "person"
                and detection.confidence >= self._confidence_threshold
                and detection.identity_confidence is not None
                and detection.identity_confidence >= self._identity_confidence_threshold
            ]
            detections.sort(
                key=lambda detection: (
                    -float(detection.identity_confidence or 0),
                    -detection.confidence,
                    detection.detection_id,
                )
            )
            for detection in detections:
                assert detection.student_id is not None
                selected.setdefault(
                    detection.student_id,
                    _IdentifiedDetection(event=event, detection=detection),
                )
        return selected
