"""Student monitoring service."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..classrooms.errors import ClassroomNotFoundError
from ..classrooms.mapping import map_detections_to_observations
from ..classrooms.models import SeatObservation
from ..classrooms.service import ClassroomService
from ..shared.broadcaster import InMemoryBroadcaster
from ..video_monitoring.ports import VideoStreamRepository
from .errors import VideoStreamNotFoundError
from .models import DetectionEvent, VideoSegment
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
    ) -> None:
        self._detection_repository = detection_repository
        self._segment_repository = segment_repository
        self._stream_repository = stream_repository
        self._broadcaster = broadcaster
        self._classroom_service = classroom_service
        self._confidence_threshold = occupancy_confidence_threshold

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
