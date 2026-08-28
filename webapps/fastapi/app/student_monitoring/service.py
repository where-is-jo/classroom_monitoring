"""Student monitoring service."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from threading import RLock
from uuid import NAMESPACE_URL, uuid5

from ..classrooms.errors import ClassroomNotFoundError
from ..classrooms.models import Seat, SeatAssignment, SeatObservation
from ..classrooms.service import ClassroomService
from ..roi_connections.errors import RoiConnectionNotFoundError
from ..roi_connections.models import RoiConnection
from ..roi_connections.service import RoiConnectionService
from ..shared.broadcaster import InMemoryBroadcaster
from ..shared.student_identity import StudentIdentity, StudentLookupPort
from ..video_monitoring.errors import VideoStreamNotFoundError
from ..video_monitoring.models import CameraRole
from ..video_monitoring.ports import VideoStreamRepository
from .models import (
    Detection,
    DetectionEvent,
    SeatEvidence,
    StudentSeatState,
    StudentState,
    StudentStateHistory,
    StudentStateReason,
    StudentStateRecord,
    VideoSegment,
)
from .occupancy_mapping import (
    map_detections_to_evidence,
    to_seat_observations,
    unseated_identities,
)
from .ports import (
    DetectionEventRepository,
    StudentStateRepository,
    VideoSegmentRepository,
)
from .state_rules import (
    StatePolicy,
    StudentAssignment,
    decide_student_states,
    project_for_display,
)

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
        state_repository: StudentStateRepository,
        broadcaster: InMemoryBroadcaster,
        classroom_service: ClassroomService,
        roi_service: RoiConnectionService,
        *,
        occupancy_confidence_threshold: float,
        occupancy_hold_seconds: float,
        identity_confidence_threshold: float,
        identity_hold_seconds: float,
        absent_grace_seconds: float,
        stale_seconds: int,
        history_limit: int,
        clock: Callable[[], datetime],
        student_lookup: StudentLookupPort,
    ) -> None:
        self._detection_repository = detection_repository
        self._segment_repository = segment_repository
        self._stream_repository = stream_repository
        self._state_repository = state_repository
        self._broadcaster = broadcaster
        self._classroom_service = classroom_service
        self._roi_service = roi_service
        self._confidence_threshold = occupancy_confidence_threshold
        self._hold_seconds = occupancy_hold_seconds
        # (camera_id, seat_id) -> 마지막으로 점유를 관측한 시각과 그때의 신뢰도.
        #
        # 저장소가 아니라 메모리에 두는 이유는 이것이 수 초짜리 판정 보조 상태이기
        # 때문이다. 프로세스를 다시 띄우면 사라지고, 다음 탐지 이벤트에서 곧바로
        # 다시 쌓인다. 다만 fastapi를 여러 프로세스로 늘리면 프로세스마다 따로
        # 쌓이므로 붙드는 구간이 갈릴 수 있다 — SSE broadcaster와 같은 제약이다.
        self._last_seen: dict[tuple[str, str], tuple[datetime, float]] = {}
        self._last_seen_lock = RLock()
        self._identity_confidence_threshold = identity_confidence_threshold
        self._stale_seconds = stale_seconds
        self._history_limit = history_limit
        self._clock = clock
        self._student_lookup = student_lookup
        self._policy = StatePolicy(
            occupancy_confidence_threshold=occupancy_confidence_threshold,
            identity_confidence_threshold=identity_confidence_threshold,
            identity_hold_seconds=identity_hold_seconds,
            absent_grace_seconds=absent_grace_seconds,
            stale_seconds=stale_seconds,
        )

    def _held_seats(
        self, camera_id: str, seat_ids: list[str], observed_at: datetime
    ) -> dict[str, float]:
        """아직 붙들어 둘 좌석과 그때의 신뢰도를 돌려준다.

        붙드는 것은 "최근에 실제로 봤다"는 근거가 있을 때뿐이다. 유지 시간이 지나면
        더 이상 붙들지 않고 비어 있음으로 넘어간다 — 자리를 뜬 사람을 계속 앉아 있다고
        기록하지 않기 위해서다.
        """
        if self._hold_seconds <= 0:
            return {}
        deadline = observed_at - timedelta(seconds=self._hold_seconds)
        held: dict[str, float] = {}
        with self._last_seen_lock:
            for seat_id in seat_ids:
                entry = self._last_seen.get((camera_id, seat_id))
                if entry is not None and entry[0] >= deadline:
                    held[seat_id] = entry[1]
        return held

    def _remember_seen(
        self,
        camera_id: str,
        observations: tuple[SeatObservation, ...],
        observed_at: datetime,
        held: Mapping[str, float],
    ) -> None:
        """이번 프레임에서 **임계값 이상으로 실제 탐지된** 좌석의 관측 시각을 남긴다.

        두 가지를 제외한다.

        - 붙들려서 점유가 된 좌석. 그것까지 갱신하면 한 번 잡힌 좌석이 유지 시간을
          계속 갱신받아 영영 점유로 남는다.
        - 임계값 미만 탐지만 있던 좌석. 그 관측은 `UNKNOWN`으로 가는 약한 근거이고,
          "방금 확실히 봤다"로 취급해 유지 시간을 늘려 줄 자격이 없다.

        늦게 도착한 오래된 프레임이 기록을 과거로 되돌리지 않는다. 되돌리면 유지 시간이
        실제보다 일찍 만료돼 앉아 있는 사람의 좌석이 깜빡인다.
        """
        with self._last_seen_lock:
            for observation in observations:
                if (
                    not observation.occupied
                    or observation.seat_id in held
                    or observation.confidence < self._confidence_threshold
                ):
                    continue
                key = (camera_id, observation.seat_id)
                previous = self._last_seen.get(key)
                if previous is not None and previous[0] > observed_at:
                    continue
                self._last_seen[key] = (observed_at, observation.confidence)

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

        # **bbox overlay를 먼저 내보낸다.** 화면에 상자를 그리는 데 저장소는 필요
        # 없는데, 예전에는 재수신 확인·저장·마지막 탐지 갱신을 모두 마친 뒤에야
        # 발행했다. 이 저장소는 원격 Atlas라 그 세 번이 왕복 세 번이고, 회선이
        # 흔들리면 그대로 곱해진다(결정 0045의 실측: 왕복 41ms~118ms).
        #
        # 재수신이면 같은 상자를 한 번 더 그린다. 덮어 그리는 것이라 화면은 같고,
        # 저장 여부를 확인하려고 오버레이를 늦추는 것보다 낫다.
        self._publish_detection_overlay(resolved_event)

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

            # 탐지→좌석 매핑: 새 이벤트일 때만 좌석 관측 batch를 만들고,
            # 반영된 좌석 상태를 occupancy SSE로 발행한다.
            # 같은 event_id를 다시 받으면 batch 멱등 처리와 어긋나는 관측이 생길 수 없도록
            # 재수신에서는 매핑을 반복하지 않는다.
            # 매핑·batch 기록·SSE 발행이 실패해도 탐지 이벤트 저장 결과는 그대로 돌려준다.
            #
            # 관측 범위는 이 카메라에 ROI가 등록된 좌석뿐이다(결정 0020). 강의실을
            # 나눠 보는 구성에서 다른 카메라 담당 좌석까지 "비어 있음"으로 덮어쓰지
            # 않게 하려는 것이다. ROI가 하나도 없으면 관측을 만들지 않는다.
            # 탐지가 0건이어도 관측을 만든다. 사람이 하나도 안 잡힌 프레임은 "볼 것이
            # 없었다"가 아니라 "그 카메라가 보는 좌석이 전부 비어 있다"는 관측이다.
            # 이것을 건너뛰면 마지막 사람이 나간 뒤 좌석이 점유인 채로 얼어붙고,
            # 유지 시간(hold)도 다음 탐지가 올 때까지 만료되지 않는다.
            #
            # 신원 전용 카메라(입구)는 좌석 판정에 참여하지 않는다(결정 0024의 3번).
            # 좌석을 담지 않는 화각의 이벤트가 "최신"이라는 이유로 직전 판정을 UNKNOWN
            # 으로 덮는 것을 막는다. 입구 신원은 worker가 문 영역·통과 시각으로 CCTV
            # ByteTrack에 안전하게 인계하며, FastAPI에는 CCTV 이벤트로 들어온다(0036).
            if saved_event.classroom_id and stream.role is CameraRole.SEAT_JUDGING:
                classroom_id = saved_event.classroom_id
                try:
                    connections = self._roi_service.list_valid_connections(
                        classroom_id, saved_event.camera_id
                    )
                    held = self._held_seats(
                        saved_event.camera_id,
                        [connection.seat_id for connection in connections],
                        saved_event.captured_at,
                    )
                    evidence = map_detections_to_evidence(
                        saved_event.detections,
                        connections,
                        saved_event.frame,
                        self._confidence_threshold,
                        held=held,
                    )
                    observations = to_seat_observations(evidence)
                    self._remember_seen(
                        saved_event.camera_id, observations, saved_event.captured_at, held
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
                    # 좌석 근거 하나로 학생 상태까지 판정한다. 같은 값에서 갈라지므로
                    # 좌석 화면과 학생 화면이 서로 어긋날 수 없다.
                    self._evaluate_student_states(
                        classroom_id=classroom_id,
                        event=saved_event,
                        evidence=evidence,
                        connections=connections,
                    )
                except ClassroomNotFoundError:
                    logger.warning(
                        "event_id=%s camera_id=%s classroom_id=%s "
                        "활성 강의실 참조가 없어 좌석 매핑을 건너뜁니다.",
                        saved_event.event_id,
                        saved_event.camera_id,
                        classroom_id,
                    )
                except RoiConnectionNotFoundError:
                    # ROI 미등록은 오류가 아니라 아직 설정하지 않은 상태다.
                    # 좌석을 추정하지 않고 관측을 만들지 않는다(결정 0020).
                    logger.warning(
                        "event_id=%s camera_id=%s classroom_id=%s "
                        "등록된 좌석 ROI가 없어 좌석 관측을 만들지 않습니다.",
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

        return InferenceEventResult(event=saved_event, is_new=is_new)

    def publish_overlay(self, event: DetectionEvent) -> None:
        """bbox overlay만 구독자에게 내보낸다. 저장소를 전혀 쓰지 않는다.

        `receive_inference_event`와 달리 강의실 확인도 하지 않는다. 구독자는
        `camera_id`로만 걸러 받으므로 등록되지 않은 카메라의 payload는 아무에게도
        닿지 않고, 확인하려면 저장소 왕복이 생겨 이 경로를 만든 이유가 없어진다.
        """
        self._publish_detection_overlay(event)

    def _publish_detection_overlay(self, event: DetectionEvent) -> None:
        """bbox overlay용 SSE를 내보낸다. 저장소를 거치지 않는다.

        구독자는 `camera_id`로만 걸러 받으므로(`stream_detection_events`) 저장 결과인
        `stream_id`·`classroom_id`는 payload에 필요 없다. frame과 detections를 함께
        보내야 브라우저가 화면 크기에 맞춰 상자를 환산할 수 있다.

        **발행 실패가 탐지 저장을 막지 않는다.** 오버레이는 놓쳐도 다음 프레임이
        덮어 그리지만, 저장은 그 이벤트가 유일한 기회다.
        """
        try:
            self._broadcaster.publish(
                {
                    "type": "detection",
                    "event_id": event.event_id,
                    "camera_id": event.camera_id,
                    "captured_at": event.captured_at.isoformat(),
                    "sequence": event.sequence,
                    "frame": {
                        "width_pixels": event.frame.width_pixels,
                        "height_pixels": event.frame.height_pixels,
                    },
                    "detections": [
                        {
                            "detection_id": d.detection_id,
                            "class_id": d.class_id,
                            "class_name": d.class_name,
                            "confidence": d.confidence,
                            "bbox": list(d.bbox),
                            "student_id": d.student_id,
                            "track_id": d.track_id,
                            "display_label": self._safe_detection_label(d),
                        }
                        for d in event.detections
                    ],
                    "detections_count": len(event.detections),
                }
            )
        except Exception:
            logger.exception("bbox overlay 발행에 실패했습니다. event_id=%s", event.event_id)

    def _safe_detection_label(self, detection: Detection) -> str:
        """활성 학생의 임계값 이상 식별만 이름으로 표시한다."""
        if (
            detection.class_name.casefold() != "person"
            or detection.confidence < self._confidence_threshold
            or detection.student_id is None
            or detection.identity_confidence is None
            or detection.identity_confidence < self._identity_confidence_threshold
        ):
            return "인식 불가"
        try:
            student = self._student_lookup.find_by_id(detection.student_id)
        except Exception:
            logger.exception(
                "student_id=%s 탐지 라벨 보강 중 학생 조회에 실패했습니다.",
                detection.student_id,
            )
            return "인식 불가"
        return student.name if student is not None and student.is_active else "인식 불가"

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

    def _evaluate_student_states(
        self,
        *,
        classroom_id: str,
        event: DetectionEvent,
        evidence: tuple[SeatEvidence, ...],
        connections: Sequence[RoiConnection],
    ) -> None:
        """좌석 근거로 학생 상태를 판정해 저장하고, 바뀐 것만 이력·SSE로 남긴다.

        판정은 **탐지 이벤트를 받을 때만** 한다. 조회는 저장된 결과를 읽기만 하므로
        화면을 두 번 연 것과 한 번 연 것의 결과가 다르지 않다(결정 0008).
        """
        seats = self._classroom_service.list_all_seats(classroom_id)
        raw_assignments = self._classroom_service.list_assignments_raw(classroom_id)
        active = self._active_assignments(classroom_id, seats, raw_assignments)
        if not active:
            return

        previous = {
            record.student_id: record
            for record in self._state_repository.list_by_classroom(classroom_id)
        }
        unseated = unseated_identities(
            event.detections,
            connections,
            event.frame,
            self._confidence_threshold,
        )
        decided = decide_student_states(
            assignments=[
                StudentAssignment(student_id=student.id, seat_id=assignment.seat_id)
                for assignment, _, student in active
            ],
            evidence=evidence,
            unseated=unseated,
            previous=previous,
            classroom_id=classroom_id,
            event_id=event.event_id,
            observed_at=event.captured_at,
            policy=self._policy,
        )

        seats_by_id = {seat.id: seat for seat in seats}
        students = {student.id: student for _, _, student in active}
        recorded_at = self._clock()
        for record in decided:
            before = previous.get(record.student_id)
            if before is not None and before.observed_at > record.observed_at:
                # 늦게 도착한 오래된 프레임이 최신 판정을 되돌리지 않는다.
                continue
            self._state_repository.save(record)
            # 판정한 적 없는 학생의 기본값은 UNKNOWN이다. 첫 판정이 UNKNOWN이면 바뀐
            # 것이 없으므로 이력도 SSE도 만들지 않는다 — 아무 일도 없었다는 사실을
            # 전이로 기록하면 이력이 잡음으로 찬다.
            previous_state = StudentState.UNKNOWN if before is None else before.state
            if previous_state == record.state:
                continue
            self._state_repository.append_history(
                StudentStateHistory(
                    id=str(
                        uuid5(
                            NAMESPACE_URL,
                            f"student-state-history:{record.event_id}:{record.student_id}",
                        )
                    ),
                    student_id=record.student_id,
                    classroom_id=classroom_id,
                    event_id=record.event_id,
                    from_state=previous_state,
                    to_state=record.state,
                    reason=record.reason,
                    seat_id=record.seat_id,
                    confidence=record.confidence,
                    observed_at=record.observed_at,
                    recorded_at=recorded_at,
                )
            )
            student = students.get(record.student_id)
            if student is None:
                continue
            self._publish_student_state(record, student=student, seats_by_id=seats_by_id)

    def _publish_student_state(
        self,
        record: StudentStateRecord,
        *,
        student: StudentIdentity,
        seats_by_id: Mapping[str, Seat],
    ) -> None:
        """상태가 바뀐 학생만 SSE로 발행한다."""
        assigned_seat = (
            None if record.assigned_seat_id is None else seats_by_id.get(record.assigned_seat_id)
        )
        current_seat = None if record.seat_id is None else seats_by_id.get(record.seat_id)
        self._broadcaster.publish(
            {
                "type": "student-state",
                "event_id": record.event_id,
                "classroom_id": record.classroom_id,
                "student_id": record.student_id,
                "student_name": student.name,
                "student_no": student.student_no,
                "assigned_seat_id": record.assigned_seat_id,
                "assigned_seat_label": (None if assigned_seat is None else assigned_seat.label),
                "current_seat_id": record.seat_id,
                "current_seat_label": None if current_seat is None else current_seat.label,
                "current_state": record.state.value,
                "reason": record.reason.value,
                "confidence": record.confidence,
                "observed_at": record.observed_at.isoformat(),
            }
        )

    def list_student_states(self, classroom_id: str) -> list[StudentSeatState]:
        """저장된 판정 결과를 화면·API가 쓸 읽기 모델로 옮긴다.

        **여기서 판정하지 않는다.** 좌석 지정과 학생 원장을 붙여 표시용 값을 만들 뿐이고,
        근거가 오래된 판정은 `project_for_display`가 `UNKNOWN`으로 가린다. 가리기만
        하고 저장된 값을 바꾸지 않는다.
        """
        seats = self._classroom_service.list_all_seats(classroom_id)
        assignments = self._classroom_service.list_assignments_raw(classroom_id)
        active = self._active_assignments(classroom_id, seats, assignments)
        if not active:
            return []

        seats_by_id = {seat.id: seat for seat in seats}
        records = {
            record.student_id: record
            for record in self._state_repository.list_by_classroom(classroom_id)
        }
        now = self._clock()

        result: list[StudentSeatState] = []
        for assignment, seat, student in active:
            record = records.get(student.id)
            if record is None:
                # 아직 이 학생을 두고 판정한 적이 없다.
                result.append(
                    StudentSeatState(
                        student_id=student.id,
                        student_name=student.name,
                        student_no=student.student_no,
                        assigned_seat_id=assignment.seat_id,
                        assigned_seat_label=seat.label,
                        current_seat_id=None,
                        current_seat_label=None,
                        current_state=StudentState.UNKNOWN,
                        reason=StudentStateReason.SEAT_NOT_OBSERVED,
                        confidence=None,
                        last_observed_at=None,
                    )
                )
                continue
            display = project_for_display(record, now, self._policy)
            current_seat = None if display.seat_id is None else seats_by_id.get(display.seat_id)
            result.append(
                StudentSeatState(
                    student_id=student.id,
                    student_name=student.name,
                    student_no=student.student_no,
                    assigned_seat_id=assignment.seat_id,
                    assigned_seat_label=seat.label,
                    current_seat_id=display.seat_id,
                    current_seat_label=None if current_seat is None else current_seat.label,
                    current_state=display.state,
                    reason=display.reason,
                    confidence=display.confidence,
                    last_observed_at=record.observed_at,
                )
            )
        return result

    def list_student_state_history(
        self, classroom_id: str, student_id: str
    ) -> list[StudentStateHistory]:
        """학생의 상태 전이 이력을 최신순으로 반환한다.

        출결 판정의 근거를 되짚기 위한 경로다(결정 0008).
        """
        return self._state_repository.list_history(
            classroom_id, student_id, limit=self._history_limit
        )

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
