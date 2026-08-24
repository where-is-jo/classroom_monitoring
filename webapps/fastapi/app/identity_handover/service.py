"""입구 얼굴 신원을 CCTV ByteTrack으로 넘기는 route 설정."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from threading import RLock

from ..classrooms.models import Classroom
from ..roi_connections.errors import CameraFrameUnavailableError
from ..roi_connections.ports import CameraFrameGrabber
from ..roi_connections.service import RoiConnectionService
from ..video_monitoring.models import CameraRole, VideoStream
from .errors import (
    IdentityHandoverRouteInputError,
    IdentityHandoverRouteNotFoundError,
)
from .models import (
    HandoverCameraOption,
    HandoverPageOptions,
    HandoverReferenceImage,
    HandoverZone,
    IdentityHandoverRoute,
    SaveIdentityHandoverRouteCommand,
)
from .ports import IdentityHandoverRouteRepository


class IdentityHandoverRouteService:
    def __init__(
        self,
        repository: IdentityHandoverRouteRepository,
        roi_service: RoiConnectionService,
        frame_grabber: CameraFrameGrabber,
        *,
        max_image_bytes: int,
        clock: Callable[[], datetime],
    ) -> None:
        self._repository = repository
        self._roi = roi_service
        self._frames = frame_grabber
        self._max_image_bytes = max_image_bytes
        self._clock = clock
        self._images: dict[tuple[str, str], HandoverReferenceImage] = {}
        self._image_lock = RLock()

    def list_classrooms(self) -> list[Classroom]:
        return self._roi.list_classrooms()

    def get_classroom(self, classroom_id: str) -> Classroom:
        return self._roi.get_classroom(classroom_id)

    def page_options(self, classroom_id: str) -> HandoverPageOptions:
        streams = self._roi.list_streams(classroom_id)
        capture_by_camera = {
            option.camera_id: option.capture_available
            for option in self._roi.list_camera_options(classroom_id)
        }

        def option(stream: VideoStream) -> HandoverCameraOption:
            return HandoverCameraOption(
                camera_id=stream.camera_id,
                camera_label=stream.camera_label,
                capture_available=capture_by_camera.get(stream.camera_id, False),
            )

        return HandoverPageOptions(
            entry_cameras=tuple(
                option(stream) for stream in streams if stream.role == CameraRole.IDENTITY_ONLY
            ),
            classroom_cameras=tuple(
                option(stream) for stream in streams if stream.role == CameraRole.SEAT_JUDGING
            ),
        )

    def list_routes(self, classroom_id: str) -> list[IdentityHandoverRoute]:
        self.get_classroom(classroom_id)
        return sorted(
            self._repository.list_by_classroom(classroom_id),
            key=lambda route: (route.classroom_camera_id, route.entry_camera_id),
        )

    def capture_reference_image(
        self, classroom_id: str, classroom_camera_id: str
    ) -> HandoverReferenceImage:
        self._required_classroom_camera(classroom_id, classroom_camera_id)
        content = self._frames.capture_jpeg(classroom_camera_id)
        if not content or not content.startswith(b"\xff\xd8\xff"):
            raise CameraFrameUnavailableError("CCTV에서 JPEG 기준 화면을 가져오지 못했습니다.")
        if len(content) > self._max_image_bytes:
            raise CameraFrameUnavailableError(
                "CCTV 화면이 허용 크기를 넘어 기준 이미지로 쓸 수 없습니다."
            )
        with self._image_lock:
            key = (classroom_id, classroom_camera_id)
            previous = self._images.get(key)
            revision = 1 if previous is None else previous.revision + 1
            image = HandoverReferenceImage(
                classroom_id=classroom_id,
                camera_id=classroom_camera_id,
                content=content,
                display_name=(
                    f"{classroom_camera_id} {self._clock().strftime('%Y%m%d-%H%M%S')} 캡처"
                ),
                revision=revision,
            )
            self._images[key] = image
            return image

    def get_reference_image(
        self, classroom_id: str, classroom_camera_id: str
    ) -> HandoverReferenceImage:
        self._required_classroom_camera(classroom_id, classroom_camera_id)
        with self._image_lock:
            image = self._images.get((classroom_id, classroom_camera_id))
        if image is None:
            raise IdentityHandoverRouteNotFoundError("캡처된 CCTV 인계 기준 화면이 없습니다.")
        return image

    def list_active_routes(self) -> list[IdentityHandoverRoute]:
        """worker가 안전하게 적용할 수 있는 현재 route만 반환한다.

        저장 뒤 카메라가 비활성화되거나 역할·강의실 연결이 바뀌면 잘못된 두 화각을
        이어 붙이지 않는다. 저장 문서는 관리 화면에 남겨 보이되 worker 입력에서는 뺀다.
        """
        active: list[IdentityHandoverRoute] = []
        for route in self._repository.list_all():
            try:
                entry, classroom = self._required_stream_pair(
                    route.classroom_id,
                    route.entry_camera_id,
                    route.classroom_camera_id,
                )
            except IdentityHandoverRouteInputError:
                continue
            if entry.enabled and classroom.enabled:
                active.append(route)
        return sorted(active, key=lambda route: route.classroom_camera_id)

    def save_route(self, command: SaveIdentityHandoverRouteCommand) -> IdentityHandoverRoute:
        self._required_stream_pair(
            command.classroom_id,
            command.entry_camera_id,
            command.classroom_camera_id,
        )
        _validate_zone(command.classroom_entry_zone)
        with self._image_lock:
            image = self._images.get((command.classroom_id, command.classroom_camera_id))
        if image is None:
            raise IdentityHandoverRouteInputError(
                "CCTV 현재 화면을 먼저 캡처한 뒤 인계 영역을 그려 주세요."
            )
        if image.revision != command.reference_image_revision:
            raise IdentityHandoverRouteInputError(
                "CCTV 기준 화면이 바뀌었습니다. 현재 화면에서 영역을 다시 확인해 주세요."
            )
        return self._repository.save(
            IdentityHandoverRoute(
                classroom_id=command.classroom_id,
                entry_camera_id=command.entry_camera_id,
                classroom_camera_id=command.classroom_camera_id,
                classroom_entry_zone=command.classroom_entry_zone,
                reference_image_revision=command.reference_image_revision,
                updated_at=self._clock(),
            )
        )

    def _required_classroom_camera(
        self, classroom_id: str, classroom_camera_id: str
    ) -> VideoStream:
        self.get_classroom(classroom_id)
        classroom = next(
            (
                stream
                for stream in self._roi.list_streams(classroom_id)
                if stream.camera_id == classroom_camera_id
            ),
            None,
        )
        if classroom is None or classroom.role != CameraRole.SEAT_JUDGING:
            raise IdentityHandoverRouteInputError(
                "선택한 CCTV는 활성 SEAT_JUDGING 카메라가 아닙니다."
            )
        return classroom

    def delete_route(self, classroom_id: str, classroom_camera_id: str) -> None:
        self.get_classroom(classroom_id)
        if not self._repository.delete(classroom_id, classroom_camera_id):
            raise IdentityHandoverRouteNotFoundError()

    def _required_stream_pair(
        self,
        classroom_id: str,
        entry_camera_id: str,
        classroom_camera_id: str,
    ) -> tuple[VideoStream, VideoStream]:
        self.get_classroom(classroom_id)
        if entry_camera_id == classroom_camera_id:
            raise IdentityHandoverRouteInputError("입구 카메라와 강의실 CCTV는 서로 달라야 합니다.")
        streams = {stream.camera_id: stream for stream in self._roi.list_streams(classroom_id)}
        entry = streams.get(entry_camera_id)
        classroom = streams.get(classroom_camera_id)
        if entry is None or entry.role != CameraRole.IDENTITY_ONLY:
            raise IdentityHandoverRouteInputError(
                "선택한 입구 카메라는 활성 IDENTITY_ONLY 카메라가 아닙니다."
            )
        if classroom is None or classroom.role != CameraRole.SEAT_JUDGING:
            raise IdentityHandoverRouteInputError(
                "선택한 CCTV는 활성 SEAT_JUDGING 카메라가 아닙니다."
            )
        return entry, classroom


def _validate_zone(zone: HandoverZone) -> None:
    left, top, right, bottom = zone.as_tuple()
    if any(value < 0 or value > 1 for value in zone.as_tuple()):
        raise IdentityHandoverRouteInputError("인계 영역 좌표는 0과 1 사이여야 합니다.")
    if left >= right or top >= bottom:
        raise IdentityHandoverRouteInputError("인계 영역은 넓이가 있는 사각형이어야 합니다.")
