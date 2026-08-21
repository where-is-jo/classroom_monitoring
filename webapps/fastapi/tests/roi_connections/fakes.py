"""ROI 테스트가 카메라 장비 없이 돌게 하는 대역."""

from __future__ import annotations

from app.roi_connections.errors import CameraFrameUnavailableError

# 최소 JPEG 서명. 서비스가 형식 서명을 검사하므로 아무 bytes나 쓸 수 없다.
JPEG_BYTES = b"\xff\xd8\xff" + b"fake-frame"


class FakeCameraFrameGrabber:
    """정해 둔 카메라에서 고정된 JPEG를 돌려준다.

    호출 횟수를 세는 이유는 "캡처할 때마다 새로 붙는다"는 계약을 검증하기 위해서다.
    """

    def __init__(
        self,
        available: set[str] | None = None,
        *,
        frame: bytes = JPEG_BYTES,
        fail: bool = False,
    ) -> None:
        self.available = available if available is not None else {"camera-a"}
        self.frame = frame
        self.fail = fail
        self.calls: list[str] = []

    def is_available(self, camera_id: str) -> bool:
        return camera_id in self.available

    def capture_jpeg(self, camera_id: str) -> bytes:
        self.calls.append(camera_id)
        if self.fail or camera_id not in self.available:
            raise CameraFrameUnavailableError("카메라에서 현재 화면을 가져오지 못했습니다.")
        return self.frame
