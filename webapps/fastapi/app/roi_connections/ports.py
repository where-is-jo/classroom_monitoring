"""ROI 연결 영속 저장소 포트."""

from typing import Protocol

from .models import RoiConnection


class RoiConnectionRepository(Protocol):
    def list_by_classroom(self, classroom_id: str) -> list[RoiConnection]: ...

    def list_by_camera(self, classroom_id: str, camera_id: str) -> list[RoiConnection]: ...

    def find_by_student(
        self, classroom_id: str, camera_id: str, student_id: str
    ) -> RoiConnection | None: ...

    def save(self, connection: RoiConnection) -> RoiConnection: ...
