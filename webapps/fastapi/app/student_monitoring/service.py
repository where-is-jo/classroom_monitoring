"""Student monitoring service."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from ..classrooms.errors import ClassroomNotFoundError
from ..classrooms.mapping import find_seat_for_detection, map_detections_to_observations
from ..classrooms.models import Seat, SeatAssignment, SeatObservation
from ..classrooms.service import ClassroomService
from ..shared.broadcaster import InMemoryBroadcaster
from ..shared.student_identity import StudentLookupPort
from ..video_monitoring.ports import VideoStreamRepository
from .errors import VideoStreamNotFoundError
from .models import (
    Detection,
    DetectionEvent,
    FrameInfo,
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


class StudentMonitoringService:
    """Student monitoring service."""

    def __init__(
        self,
        detection_repository: DetectionEventRepository,
        segment_repository: VideoSegmentRepository,
        stream_repository: VideoStreamRepository,
        broadcaster: InMemoryBroadcaster,
        classroom_service: ClassroomService,
        *,
        occupancy_confidence_threshold: float,
        identity_confidence_threshold: float = 0.5,  # R9: 신뢰도 임계값
        student_lookup: StudentLookupPort | None = None,  # 학생 이름 조회용 (중립 계약)
    ) -> None:
        self._detection_repository = detection_repository
        self._segment_repository = segment_repository
        self._stream_repository = stream_repository
        self._broadcaster = broadcaster
        self._classroom_service = classroom_service
        self._confidence_threshold = occupancy_confidence_threshold
        self._identity_confidence_threshold = identity_confidence_threshold
        self._student_lookup = student_lookup

    def receive_inference_event(self, event: DetectionEvent) -> InferenceEventResult:
        """Receive inference event."""
        # Check camera exists
        stream = self._stream_repository.find_by_camera_id(event.camera_id)
        if stream is None:
            raise VideoStreamNotFoundError()

        # Check if event already exists
        existing = self._detection_repository.find_by_event_id(event.event_id)
        is_new = existing is None

        # Save event (idempotent)
        saved_event = self._detection_repository.save(event)

        # Update last detection timestamp only for new events
        if is_new:
            self._stream_repository.update_last_detection(event.camera_id, event.captured_at)

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
            if event.detections and stream.classroom_id:
                classroom_id = stream.classroom_id
                try:
                    seats = self._classroom_service.list_all_seats(classroom_id)
                    observations = map_detections_to_observations(
                        event.detections,
                        seats,
                        event.frame,
                        self._confidence_threshold,
                    )
                    if observations:
                        self._classroom_service.record_seat_observation_batch(
                            event_id=event.event_id,
                            classroom_id=classroom_id,
                            observations=observations,
                            observed_at=event.captured_at,
                        )
                        self._publish_occupancy_events(
                            event_id=event.event_id,
                            classroom_id=classroom_id,
                            observations=observations,
                        )
                except ClassroomNotFoundError:
                    logger.warning("강의실 %s를 찾을 수 없어 좌석 매핑을 건너뜁니다.", classroom_id)
                except Exception:
                    logger.exception(
                        "event_id=%s 좌석 매핑 중 오류가 발생해 건너뜁니다.", event.event_id
                    )

        return InferenceEventResult(event=saved_event, is_new=is_new)

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

    def _judge_student_states(
        self,
        detections: Sequence[Detection],
        seats: Sequence[Seat],
        assignments: Sequence[SeatAssignment],
        frame: FrameInfo,
    ) -> list[StudentSeatState]:
        """탐지 결과와 좌석-학생 지정을 비교해 학생 상태를 판정한다.

        판정 규칙:
        1. identity_confidence가 임계값 미달인 탐지 → UNKNOWN (R9)
        2. student_id가 null인 탐지 → UNKNOWN
        3. student_id가 있지만 지정 좌석이 없는 학생 → UNKNOWN
        4. 탐지 위치(bbox 중심점)가 지정 좌석과 일치 → PRESENT
        5. 탐지 위치가 다른 좌석에 있음 → WRONG_SEAT

        참고: 수업 시간 게이트(R8)는 현재 class_sessions 미구현으로 항상 판정 수행.
        """
        result: list[StudentSeatState] = []
        processed_students: set[str] = set()

        for detection in detections:
            # 신뢰도 임계값 미달 → UNKNOWN (R9)
            if (
                detection.identity_confidence is not None
                and detection.identity_confidence < self._identity_confidence_threshold
            ):
                continue

            # student_id가 없으면 UNKNOWN (REQ-012)
            if detection.student_id is None:
                continue

            # 이미 처리한 학생이면 건너뜀 (같은 학생 두 곳 탐지 시 먼저 탐지된 쪽만 채택)
            if detection.student_id in processed_students:
                continue
            processed_students.add(detection.student_id)

            # 지정 좌석 조회
            assigned: SeatAssignment | None = None
            for a in assignments:
                if a.student_id == detection.student_id:
                    assigned = a
                    break

            # 지정 좌석이 없으면 UNKNOWN
            if assigned is None:
                continue

            # 현재 탐지 위치가 어떤 좌석인지 조회
            current_seat = find_seat_for_detection(
                detection, seats, frame.width_pixels, frame.height_pixels
            )

            # 상태 판정
            if current_seat is not None and current_seat.id == assigned.seat_id:
                state = StudentState.PRESENT
            elif current_seat is not None:
                state = StudentState.WRONG_SEAT
            else:
                state = StudentState.UNKNOWN

            # 학생 이름·학번 조회 (StudentLookupPort가 있으면)
            # unknown/inactive는 blank name/no로 판단하고 throw하지 않는다.
            student_name = ""
            student_no = ""
            if self._student_lookup is not None:
                student = self._student_lookup.find_by_id(detection.student_id)
                if student is not None and student.is_active:
                    student_name = student.name
                    student_no = student.student_no

            # 좌석 라벨 조회
            seat_label = ""
            for seat in seats:
                if seat.id == assigned.seat_id:
                    seat_label = seat.label
                    break

            result.append(
                StudentSeatState(
                    student_id=detection.student_id,
                    student_name=student_name,
                    student_no=student_no,
                    assigned_seat_id=assigned.seat_id,
                    assigned_seat_label=seat_label,
                    current_seat_id=current_seat.id if current_seat else None,
                    current_state=state,
                    confidence=detection.confidence,
                    last_observed_at=None,
                )
            )

        return result

    def list_student_states(self, classroom_id: str) -> list[StudentSeatState]:
        """강의실의 학생별 현재 상태를 반환한다.

        TASK-A05에서는 최근 탐지 이벤트 기반 판정을 아직 연결하지 않아
        빈 목록을 반환한다. 이후 작업에서 최근 detection_events를 읽어
        _judge_student_states()로 상태를 판정해 채운다.
        """
        # 좌석·지정 조회는 판정 로직 연결 시 사용하므로 호출부만 미리 준비한다.
        # 강의실이 없으면 ClassroomNotFoundError가 발생해 404로 응답한다.
        self._classroom_service.list_all_seats(classroom_id)
        self._classroom_service.list_assignments_raw(classroom_id)
        return []
