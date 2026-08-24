"""입구 얼굴 관측 이벤트 도메인 오류."""

from __future__ import annotations

from ..shared.errors import DomainError


class EntryIdentityEventConflictError(DomainError):
    code = "ENTRY_IDENTITY_EVENT_CONFLICT"
    status_code = 409

    def __init__(self) -> None:
        super().__init__("같은 이벤트 ID에 다른 입구 얼굴 관측 본문이 있습니다.")


class EntryIdentityCameraRoleError(DomainError):
    code = "ENTRY_IDENTITY_CAMERA_ROLE_INVALID"
    status_code = 422

    def __init__(self) -> None:
        super().__init__("입구 얼굴 관측은 활성 IDENTITY_ONLY 카메라만 보낼 수 있습니다.")


class EntryIdentityQueryError(DomainError):
    code = "ENTRY_IDENTITY_QUERY_INVALID"
    status_code = 422

    def __init__(self, message: str) -> None:
        super().__init__(message)
