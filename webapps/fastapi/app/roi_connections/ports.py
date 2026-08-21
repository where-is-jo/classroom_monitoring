"""ROI 연결이 프로세스 밖으로 나갈 때 쓰는 포트."""

from typing import Protocol

from .models import RoiConnection


class RoiConnectionRepository(Protocol):
    def list_by_classroom(self, classroom_id: str) -> list[RoiConnection]: ...

    def list_by_camera(self, classroom_id: str, camera_id: str) -> list[RoiConnection]: ...

    def find_by_student(
        self, classroom_id: str, camera_id: str, student_id: str
    ) -> RoiConnection | None: ...

    def save(self, connection: RoiConnection) -> RoiConnection: ...

    def delete(self, classroom_id: str, camera_id: str, seat_id: str) -> bool:
        """지웠으면 True, 지울 것이 없었으면 False. 없는 것을 지우는 것은 오류가 아니다."""
        ...


class CameraFrameGrabber(Protocol):
    """카메라에서 현재 프레임 한 장을 JPEG로 받아 오는 포트.

    프로세스 밖(카메라 장비)으로 나가는 I/O라 포트를 둔다. 캡처 수단이 ffmpeg인지
    다른 무엇인지를 서비스가 알지 않게 하고, 테스트가 장비 없이 돌게 하기 위해서다.
    """

    def is_available(self, camera_id: str) -> bool:
        """이 카메라의 접속 정보가 설정되어 있는지 알린다."""
        ...

    def capture_jpeg(self, camera_id: str) -> bytes:
        """현재 프레임을 JPEG bytes로 돌려준다. 실패하면 CameraFrameUnavailableError."""
        ...
