"""ROI 기준 이미지와 좌석-학생 연결을 메모리에 관리한다."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from threading import RLock

from ..classrooms.models import Classroom, Seat
from ..classrooms.service import ClassroomService
from ..shared.student_identity import StudentIdentity, StudentLookupPort
from ..video_monitoring.models import VideoStream
from ..video_monitoring.ports import VideoStreamRepository
from .errors import (
    RoiConnectionConflictError,
    RoiConnectionInputError,
    RoiConnectionNotFoundError,
)
from .models import (
    Point,
    ReferenceImage,
    RoiConnection,
    RoiConnectionView,
    SaveLiveRoiConnectionCommand,
    SaveRoiConnectionCommand,
)
from .ports import RoiConnectionRepository

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png"}


class RoiConnectionService:
    """재시작하면 초기화되는 ROI 프로토타입 서비스."""

    def __init__(
        self,
        classroom_service: ClassroomService,
        student_lookup: StudentLookupPort,
        repository: RoiConnectionRepository,
        stream_repository: VideoStreamRepository,
        *,
        max_upload_bytes: int,
        page_size_max: int,
        clock: Callable[[], datetime],
    ) -> None:
        self._classrooms = classroom_service
        self._students = student_lookup
        self._repository = repository
        self._streams = stream_repository
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
