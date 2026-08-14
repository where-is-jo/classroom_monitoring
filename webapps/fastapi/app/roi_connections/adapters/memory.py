"""테스트와 memory mode용 ROI 연결 저장소."""

from threading import RLock

from ..models import RoiConnection


class InMemoryRoiConnectionRepository:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], RoiConnection] = {}
        self._lock = RLock()

    def list_by_classroom(self, classroom_id: str) -> list[RoiConnection]:
        with self._lock:
            return [item for key, item in self._items.items() if key[0] == classroom_id]

    def find_by_student(self, classroom_id: str, student_id: str) -> RoiConnection | None:
        with self._lock:
            return next(
                (
                    item
                    for item in self._items.values()
                    if item.classroom_id == classroom_id and item.student_id == student_id
                ),
                None,
            )

    def save(self, connection: RoiConnection) -> RoiConnection:
        with self._lock:
            self._items[(connection.classroom_id, connection.seat_id)] = connection
        return connection
