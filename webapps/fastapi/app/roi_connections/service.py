"""ROI 기준 이미지와 좌석-학생 연결을 메모리에 관리한다."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from threading import RLock

from ..classrooms.models import Classroom, Seat
from ..classrooms.service import ClassroomService
from ..shared.student_identity import StudentIdentity, StudentLookupPort
from ..video_monitoring.models import VideoStream
from ..video_monitoring.ports import VideoStreamRepository
from .auto_layout import MIN_AUTO_POLYGON_AREA, SeatGridCell, plan_auto_roi
from .errors import (
    CameraFrameUnavailableError,
    RoiConnectionConflictError,
    RoiConnectionInputError,
    RoiConnectionNotFoundError,
)
from .models import (
    AutoRoiOutcome,
    AutoRoiResult,
    AutoRoiSeatResult,
    ConfirmAutoRoiCommand,
    ConfirmAutoRoiResult,
    GenerateAutoRoiCommand,
    Point,
    ReferenceImage,
    RoiCameraOption,
    RoiConnection,
    RoiConnectionView,
    SaveLiveRoiConnectionCommand,
    SaveRoiConnectionCommand,
)
from .ports import CameraFrameGrabber, RoiConnectionRepository

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png"}


class RoiConnectionService:
    """재시작하면 초기화되는 ROI 프로토타입 서비스."""

    def __init__(
        self,
        classroom_service: ClassroomService,
        student_lookup: StudentLookupPort,
        repository: RoiConnectionRepository,
        stream_repository: VideoStreamRepository,
        frame_grabber: CameraFrameGrabber,
        *,
        max_upload_bytes: int,
        page_size_max: int,
        clock: Callable[[], datetime],
    ) -> None:
        self._classrooms = classroom_service
        self._students = student_lookup
        self._repository = repository
        self._streams = stream_repository
        self._frames = frame_grabber
        self._max_upload_bytes = max_upload_bytes
        self._page_size_max = page_size_max
        self._clock = clock
        self._images: dict[tuple[str, str], ReferenceImage] = {}
        self._lock = RLock()

    @property
    def max_upload_bytes(self) -> int:
        return self._max_upload_bytes

    def list_classrooms(self) -> list[Classroom]:
        return self._classrooms.list_classrooms(limit=self._page_size_max, offset=0).items

    def get_classroom(self, classroom_id: str) -> Classroom:
        return self._classrooms.get_classroom(classroom_id)

    def list_seats(self, classroom_id: str) -> list[Seat]:
        return self._classrooms.list_all_seats(classroom_id)

    def list_students(self) -> list[StudentIdentity]:
        return self._students.list_active(limit=self._page_size_max, offset=0).items

    def list_streams(self, classroom_id: str) -> list[VideoStream]:
        self.get_classroom(classroom_id)
        return [
            stream
            for stream in self._streams.find_monitoring_streams()
            if stream.classroom_id == classroom_id
        ]

    def list_camera_options(self, classroom_id: str) -> list[RoiCameraOption]:
        """화면이 그대로 그릴 수 있는 카메라 선택 항목을 만든다."""
        return [
            RoiCameraOption(
                camera_id=stream.camera_id,
                camera_label=stream.camera_label,
                capture_available=self._frames.is_available(stream.camera_id),
            )
            for stream in self.list_streams(classroom_id)
        ]

    def save_reference_image(
        self,
        classroom_id: str,
        camera_id: str,
        *,
        content_type: str | None,
        content: bytes,
        filename: str | None,
    ) -> ReferenceImage:
        self._required_camera(classroom_id, camera_id)
        if content_type not in ALLOWED_IMAGE_TYPES:
            raise RoiConnectionInputError("JPEG 또는 PNG 이미지만 첨부할 수 있습니다.")
        if not content:
            raise RoiConnectionInputError("빈 이미지는 첨부할 수 없습니다.")
        if len(content) > self._max_upload_bytes:
            raise RoiConnectionInputError(
                f"이미지 크기는 {self._max_upload_bytes // (1024 * 1024)}MB 이하여야 합니다."
            )
        if not _has_valid_image_signature(content_type, content):
            raise RoiConnectionInputError("이미지 파일 형식이 올바르지 않습니다.")
        display_name = Path(filename or "reference-image").name
        return self._store_image(
            classroom_id,
            camera_id,
            content_type=content_type,
            content=content,
            display_name=display_name,
        )

    def capture_reference_image(self, classroom_id: str, camera_id: str) -> ReferenceImage:
        """카메라의 현재 화면을 잡아 ROI 기준 이미지로 삼는다.

        ROI는 좌석이라는 움직이지 않는 영역을 그리는 일이라 정지 화면이면 충분하고,
        오히려 흔들리지 않는 편이 정확하다. 실시간 영상 재생은 모니터링 화면이 맡는다
        (결정 0031).

        캡처한 프레임에는 강의실에 있는 사람이 그대로 담긴다. 그래서 파일로 쓰지 않고
        메모리에만 두며, 다음 캡처가 이전 것을 덮어쓴다.
        """
        stream = self._required_camera(classroom_id, camera_id)
        content = self._frames.capture_jpeg(camera_id)
        if not content:
            raise CameraFrameUnavailableError("카메라에서 빈 화면을 받았습니다.")
        if len(content) > self._max_upload_bytes:
            raise CameraFrameUnavailableError(
                "카메라 화면이 허용 크기를 넘어 기준 이미지로 쓸 수 없습니다."
            )
        if not _has_valid_image_signature("image/jpeg", content):
            raise CameraFrameUnavailableError("카메라에서 받은 화면이 JPEG 형식이 아닙니다.")
        captured_at = self._clock().strftime("%Y%m%d-%H%M%S")
        return self._store_image(
            classroom_id,
            camera_id,
            content_type="image/jpeg",
            content=content,
            display_name=f"{stream.camera_label} {captured_at} 캡처",
        )

    def _store_image(
        self,
        classroom_id: str,
        camera_id: str,
        *,
        content_type: str,
        content: bytes,
        display_name: str,
    ) -> ReferenceImage:
        """기준 이미지를 바꾸고 revision을 올린다.

        revision은 "이 ROI가 어느 화면 위에서 그려졌는가"를 가리킨다. 화면이 바뀌면
        전에 그린 ROI는 다른 화각의 좌표일 수 있으므로 needs_review로 떨어져야 한다
        (결정 0019).
        """
        with self._lock:
            key = (classroom_id, camera_id)
            previous = self._images.get(key)
            saved_revision = max(
                (
                    connection.reference_image_revision
                    for connection in self._repository.list_by_camera(classroom_id, camera_id)
                ),
                default=0,
            )
            image = ReferenceImage(
                classroom_id=classroom_id,
                camera_id=camera_id,
                content_type=content_type,
                content=content,
                display_name=display_name,
                revision=(saved_revision + 1) if previous is None else previous.revision + 1,
            )
            self._images[key] = image
            return image

    def get_reference_image(self, classroom_id: str, camera_id: str) -> ReferenceImage:
        self._required_camera(classroom_id, camera_id)
        with self._lock:
            image = self._images.get((classroom_id, camera_id))
        if image is None:
            raise RoiConnectionNotFoundError("첨부된 기준 이미지가 없습니다.")
        return image

    def find_reference_image(self, classroom_id: str, camera_id: str) -> ReferenceImage | None:
        with self._lock:
            return self._images.get((classroom_id, camera_id))

    def list_connections(
        self, classroom_id: str, camera_id: str | None = None
    ) -> list[RoiConnectionView]:
        seats = self.list_seats(classroom_id)
        if camera_id is not None:
            self._required_camera(classroom_id, camera_id)
            connections = self._repository.list_by_camera(classroom_id, camera_id)
        else:
            connections = self._repository.list_by_classroom(classroom_id)
        seat_order = {seat.id: index for index, seat in enumerate(seats) if seat.is_active}
        views = [
            RoiConnectionView(
                connection=connection,
                needs_review=self._needs_review(connection),
            )
            for connection in connections
            if connection.seat_id in seat_order
        ]
        return sorted(
            views,
            key=lambda view: (
                view.connection.camera_id or "",
                seat_order[view.connection.seat_id],
            ),
        )

    def list_valid_connections(self, classroom_id: str, camera_id: str) -> list[RoiConnection]:
        return [
            view.connection
            for view in self.list_connections(classroom_id, camera_id)
            if not view.needs_review
        ]

    def delete_connection(self, classroom_id: str, camera_id: str, seat_id: str) -> None:
        """좌석 하나의 ROI를 지운다.

        잘못 그린 ROI를 고칠 방법이 없으면 관리자가 손댈 수 없는 데이터가 남는다.
        지운 좌석은 그 카메라의 관측 대상에서 빠진다 — 좌석이 사라지는 것이 아니라
        "이 카메라로는 보지 않는다"가 된다(결정 0020).
        """
        self._required_camera(classroom_id, camera_id)
        if not self._repository.delete(classroom_id, camera_id, seat_id):
            raise RoiConnectionNotFoundError("삭제할 ROI 연결이 없습니다.")

    def save_connection(self, command: SaveRoiConnectionCommand) -> RoiConnectionView:
        self._required_camera(command.classroom_id, command.camera_id)
        seats = self.list_seats(command.classroom_id)
        if not any(seat.id == command.seat_id and seat.is_active for seat in seats):
            raise RoiConnectionNotFoundError("좌석을 찾을 수 없습니다.")
        if command.student_id is not None:
            student = self._students.find_by_id(command.student_id)
            if student is None or not student.is_active:
                raise RoiConnectionNotFoundError("활성 학생을 찾을 수 없습니다.")
        _validate_polygon(command.polygon)
        with self._lock:
            image = self._images.get((command.classroom_id, command.camera_id))
            if image is None:
                raise RoiConnectionConflictError("기준 이미지를 먼저 첨부해 주세요.")
            if image.revision != command.reference_image_revision:
                raise RoiConnectionConflictError(
                    "기준 이미지가 변경되었습니다. 화면을 새로고침해 주세요."
                )
            if command.student_id is not None:
                duplicate = self._repository.find_by_student(
                    command.classroom_id, command.camera_id, command.student_id
                )
                if duplicate is not None and duplicate.seat_id != command.seat_id:
                    raise RoiConnectionConflictError(
                        "선택한 학생은 이미 다른 좌석에 연결되어 있습니다."
                    )
            connection = RoiConnection(
                classroom_id=command.classroom_id,
                camera_id=command.camera_id,
                seat_id=command.seat_id,
                student_id=command.student_id,
                polygon=command.polygon,
                reference_image_revision=command.reference_image_revision,
                updated_at=self._clock(),
            )
            connection = self._repository.save(connection)
        return RoiConnectionView(connection=connection, needs_review=False)

    def save_live_connection(self, command: SaveLiveRoiConnectionCommand) -> RoiConnectionView:
        self._required_camera(command.classroom_id, command.camera_id)
        seats = self.list_seats(command.classroom_id)
        if not any(seat.id == command.seat_id and seat.is_active for seat in seats):
            raise RoiConnectionNotFoundError("선택한 강의실의 좌석을 찾을 수 없습니다.")
        student = self._students.find_by_id(command.student_id)
        if student is None or not student.is_active:
            raise RoiConnectionNotFoundError("활성 학생을 찾을 수 없습니다.")
        _validate_polygon(command.polygon)
        with self._lock:
            duplicate = self._repository.find_by_student(
                command.classroom_id, command.camera_id, command.student_id
            )
            if duplicate is not None and duplicate.seat_id != command.seat_id:
                raise RoiConnectionConflictError(
                    "선택한 학생은 이미 다른 좌석에 연결되어 있습니다."
                )
            connection = RoiConnection(
                classroom_id=command.classroom_id,
                camera_id=command.camera_id,
                seat_id=command.seat_id,
                student_id=command.student_id,
                polygon=command.polygon,
                reference_image_revision=0,
                updated_at=self._clock(),
            )
            connection = self._repository.save(connection)
        return RoiConnectionView(connection=connection, needs_review=False)

    def generate_auto_connections(self, command: GenerateAutoRoiCommand) -> AutoRoiResult:
        """좌석 격자를 캡처 화면 위로 사영해 좌석마다 ROI를 만든다.

        `dry_run`이면 계산만 하고 저장하지 않는다. 관리자가 겹쳐 보고 확인한 뒤 저장하게
        하려는 것이다 — 격자와 실제 배치가 어긋났는지는 화면에 얹어 보기 전에는 알 수 없다.

        저장하는 경우에도 `auto_generated=True`로 남아 `needs_review`가 되므로 좌석 판정에
        들어가지 않는다. 확정은 `confirm_auto_connections`가 따로 받는다
        (결정 0020의 6번).
        """
        self._required_camera(command.classroom_id, command.camera_id)
        seats = [seat for seat in self.list_seats(command.classroom_id) if seat.is_active]
        if not seats:
            raise RoiConnectionNotFoundError("강의실에 활성 좌석이 없습니다.")
        if all(seat.row is None or seat.column is None for seat in seats):
            raise RoiConnectionInputError(
                "좌석에 행·열 좌표가 없어 자동 생성을 할 수 없습니다. "
                "좌석 관리 화면에서 좌석 배치를 먼저 등록해 주세요."
            )
        labels = {seat.id: seat.label for seat in seats}
        with self._lock:
            image = self._images.get((command.classroom_id, command.camera_id))
            if image is None:
                raise RoiConnectionConflictError("기준 화면을 먼저 캡처해 주세요.")
            if image.revision != command.reference_image_revision:
                raise RoiConnectionConflictError(
                    "기준 화면이 변경되었습니다. 화면을 새로고침해 주세요."
                )
            # 사람이 만든 ROI만 지킨다. 앞서 자동으로 만든 것은 다시 계산해 덮어쓴다 —
            # 모서리나 좌석 크기를 고쳐 다시 만드는 것이 정상적인 사용 방식이다.
            preserved = frozenset(
                connection.seat_id
                for connection in self._repository.list_by_camera(
                    command.classroom_id, command.camera_id
                )
                if not connection.auto_generated
            )
            plan = plan_auto_roi(
                cells=[
                    SeatGridCell(seat_id=seat.id, row=seat.row, column=seat.column)
                    for seat in seats
                ],
                corners=command.corners,
                preserved_seat_ids=preserved,
                seat_fill_ratio=command.seat_fill_ratio,
                min_polygon_area=MIN_AUTO_POLYGON_AREA,
            )
            results: list[AutoRoiSeatResult] = []
            for candidate in plan.candidates:
                outcome = candidate.outcome
                polygon = candidate.polygon
                if outcome is AutoRoiOutcome.GENERATED and polygon is not None:
                    try:
                        # 사람이 그린 ROI와 같은 규칙을 통과해야 저장한다. 계산으로 만든
                        # 좌표라고 검사를 건너뛰면 판정이 쓰지 못할 도형이 들어갈 수 있다.
                        _validate_polygon(polygon)
                    except RoiConnectionInputError:
                        outcome, polygon = AutoRoiOutcome.INVALID_POLYGON, None
                    else:
                        if not command.dry_run:
                            self._repository.save(
                                RoiConnection(
                                    classroom_id=command.classroom_id,
                                    camera_id=command.camera_id,
                                    seat_id=candidate.seat_id,
                                    # 학생 배정의 정본은 seat_assignments다(결정 0019의 6번).
                                    # 자동 생성은 자리만 만들고 사람을 정하지 않는다.
                                    student_id=None,
                                    polygon=polygon,
                                    reference_image_revision=image.revision,
                                    updated_at=self._clock(),
                                    auto_generated=True,
                                )
                            )
                results.append(
                    AutoRoiSeatResult(
                        seat_id=candidate.seat_id,
                        seat_label=labels.get(candidate.seat_id, candidate.seat_id),
                        outcome=outcome,
                        polygon=polygon,
                    )
                )
        return AutoRoiResult(
            classroom_id=command.classroom_id,
            camera_id=command.camera_id,
            dry_run=command.dry_run,
            grid_rows=plan.grid_rows,
            grid_columns=plan.grid_columns,
            seat_fill_ratio=command.seat_fill_ratio,
            reference_image_revision=image.revision,
            seats=tuple(results),
        )

    def confirm_auto_connections(self, command: ConfirmAutoRoiCommand) -> ConfirmAutoRoiResult:
        """자동 생성분을 관리자가 확인했다고 표시해 좌석 판정에 넣는다.

        기준 화면이 그사이 다시 캡처됐다면 확정하지 않는다. 그 좌표는 다른 화각의 것이라
        확정해 봐야 `needs_review`로 남고, 조용히 지나가면 관리자는 확정된 줄 안다.
        """
        self._required_camera(command.classroom_id, command.camera_id)
        with self._lock:
            image = self._images.get((command.classroom_id, command.camera_id))
            targets = [
                connection
                for connection in self._repository.list_by_camera(
                    command.classroom_id, command.camera_id
                )
                if connection.auto_generated
                and (command.seat_ids is None or connection.seat_id in command.seat_ids)
            ]
            if not targets:
                raise RoiConnectionNotFoundError("확정할 자동 생성 ROI가 없습니다.")
            confirmed = 0
            stale = 0
            for connection in targets:
                if image is None or image.revision != connection.reference_image_revision:
                    stale += 1
                    continue
                self._repository.save(
                    replace(connection, auto_generated=False, updated_at=self._clock())
                )
                confirmed += 1
        return ConfirmAutoRoiResult(confirmed_count=confirmed, stale_count=stale)

    def _required_camera(self, classroom_id: str, camera_id: str) -> VideoStream:
        self.get_classroom(classroom_id)
        stream = self._streams.find_by_camera_id(camera_id)
        if (
            stream is None
            or not stream.enabled
            or stream.is_demo
            or stream.classroom_id != classroom_id
        ):
            raise RoiConnectionNotFoundError("선택한 강의실의 활성 카메라를 찾을 수 없습니다.")
        return stream

    def _needs_review(self, connection: RoiConnection) -> bool:
        if connection.camera_id is None:
            return True
        if connection.auto_generated:
            # 계산으로 만든 좌표다. 관리자가 확정하기 전에는 좌석 판정에 넣지 않는다.
            return True
        if connection.reference_image_revision == 0:
            return False
        image = self.find_reference_image(connection.classroom_id, connection.camera_id)
        return image is None or image.revision != connection.reference_image_revision


def _validate_polygon(polygon: tuple[Point, ...]) -> None:
    if len(polygon) < 3:
        raise RoiConnectionInputError("ROI 꼭짓점은 3개 이상이어야 합니다.")
    if any(not 0 <= point.x <= 1 or not 0 <= point.y <= 1 for point in polygon):
        raise RoiConnectionInputError("ROI 좌표는 0과 1 사이여야 합니다.")
    if len({(point.x, point.y) for point in polygon}) != len(polygon):
        raise RoiConnectionInputError("같은 ROI 꼭짓점을 중복해서 사용할 수 없습니다.")
    if abs(_signed_area(polygon)) < 1e-9:
        raise RoiConnectionInputError("ROI는 면적이 있는 다각형이어야 합니다.")
    edges = [(polygon[i], polygon[(i + 1) % len(polygon)]) for i in range(len(polygon))]
    for first_index, first in enumerate(edges):
        for second_index, second in enumerate(edges):
            if second_index <= first_index + 1:
                continue
            if first_index == 0 and second_index == len(edges) - 1:
                continue
            if _segments_intersect(*first, *second):
                raise RoiConnectionInputError("자기 교차하는 ROI는 저장할 수 없습니다.")


def _signed_area(polygon: tuple[Point, ...]) -> float:
    return (
        sum(
            point.x * polygon[(index + 1) % len(polygon)].y
            - polygon[(index + 1) % len(polygon)].x * point.y
            for index, point in enumerate(polygon)
        )
        / 2
    )


def _segments_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    def orientation(p: Point, q: Point, r: Point) -> float:
        return (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x)

    def on_segment(p: Point, q: Point, r: Point) -> bool:
        return min(p.x, r.x) <= q.x <= max(p.x, r.x) and min(p.y, r.y) <= q.y <= max(p.y, r.y)

    first = orientation(a, b, c)
    second = orientation(a, b, d)
    third = orientation(c, d, a)
    fourth = orientation(c, d, b)
    if first * second < 0 and third * fourth < 0:
        return True
    epsilon = 1e-12
    return (
        (abs(first) < epsilon and on_segment(a, c, b))
        or (abs(second) < epsilon and on_segment(a, d, b))
        or (abs(third) < epsilon and on_segment(c, a, d))
        or (abs(fourth) < epsilon and on_segment(c, b, d))
    )


def _has_valid_image_signature(content_type: str, content: bytes) -> bool:
    if content_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    return content.startswith(b"\x89PNG\r\n\x1a\n")
