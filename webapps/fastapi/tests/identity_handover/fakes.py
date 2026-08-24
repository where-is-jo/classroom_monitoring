from __future__ import annotations

from app.roi_connections.errors import CameraFrameUnavailableError

JPEG_BYTES = b"\xff\xd8\xffhandover-frame"


class FakeCameraFrameGrabber:
    def __init__(self, available: set[str] | None = None) -> None:
        self.available = available if available is not None else {"classroom-cctv"}
        self.calls: list[str] = []

    def is_available(self, camera_id: str) -> bool:
        return camera_id in self.available

    def capture_jpeg(self, camera_id: str) -> bytes:
        self.calls.append(camera_id)
        if camera_id not in self.available:
            raise CameraFrameUnavailableError("카메라 화면을 가져오지 못했습니다.")
        return JPEG_BYTES
