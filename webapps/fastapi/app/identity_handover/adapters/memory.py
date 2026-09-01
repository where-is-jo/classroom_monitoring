"""memory mode용 신원 인계 설정 저장소."""

from threading import RLock

from ..models import IdentityHandoverRoute


class InMemoryIdentityHandoverRouteRepository:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], IdentityHandoverRoute] = {}
        self._lock = RLock()

    def list_all(self) -> list[IdentityHandoverRoute]:
        with self._lock:
            return list(self._items.values())

    def list_by_classroom(self, classroom_id: str) -> list[IdentityHandoverRoute]:
        with self._lock:
            return [item for item in self._items.values() if item.classroom_id == classroom_id]

    def find_by_classroom_camera(
        self, classroom_id: str, classroom_camera_id: str
    ) -> IdentityHandoverRoute | None:
        with self._lock:
            return self._items.get((classroom_id, classroom_camera_id))

    def save(self, route: IdentityHandoverRoute) -> IdentityHandoverRoute:
        with self._lock:
            self._items[(route.classroom_id, route.classroom_camera_id)] = route
        return route

    def delete(self, classroom_id: str, classroom_camera_id: str) -> bool:
        with self._lock:
            return self._items.pop((classroom_id, classroom_camera_id), None) is not None
