"""스냅샷 조회의 도메인 예외."""

from __future__ import annotations

from ..shared.errors import DomainError


class SnapshotStorageUnavailableError(DomainError):
    """저장소에 닿지 못했다.

    **"스냅샷이 없다"와 다르다.** 빈 목록은 정상 응답이고, 이건 조회 자체가 실패한
    것이다. 화면에서 둘을 구분해 보여줘야 운영자가 카메라 문제인지 저장소 문제인지
    판단할 수 있다.
    """

    code = "SNAPSHOT_STORAGE_UNAVAILABLE"
    status_code = 503

    def __init__(self) -> None:
        super().__init__("스냅샷 저장소를 일시적으로 사용할 수 없습니다.")


class SnapshotNotFoundError(DomainError):
    code = "SNAPSHOT_NOT_FOUND"
    status_code = 404

    def __init__(self) -> None:
        super().__init__("요청한 스냅샷을 찾을 수 없습니다.")
