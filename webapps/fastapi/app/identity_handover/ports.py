"""신원 인계 설정 저장소 포트."""

from typing import Protocol

from .models import IdentityHandoverRoute


class IdentityHandoverRouteRepository(Protocol):
    def list_all(self) -> list[IdentityHandoverRoute]: ...

    def list_by_classroom(self, classroom_id: str) -> list[IdentityHandoverRoute]: ...

    def find_by_classroom_camera(
        self, classroom_id: str, classroom_camera_id: str
    ) -> IdentityHandoverRoute | None: ...

    def save(self, route: IdentityHandoverRoute) -> IdentityHandoverRoute: ...

    def delete(self, classroom_id: str, classroom_camera_id: str) -> bool: ...
