"""ROI 테스트가 카메라 장비 없이 돌게 하는 대역."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from app.roi_connections.detection_layout import DetectionSample
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


class FakeSeatedDetectionSource:
    """탐지 표본 대역. 정해 둔 표본을 그대로 돌려준다.

    ROI 자리 찾기(결정 0041)는 탐지 기록을 읽는다. 대부분의 테스트는 그 경로를 쓰지
    않으므로 빈 표본을 주고, 자리 찾기를 검증하는 테스트만 표본을 채워 넣는다.
    """

    def __init__(self, samples: Sequence[DetectionSample] = ()) -> None:
        self.samples = list(samples)
        self.calls: list[tuple[str, datetime, datetime]] = []

    def list_recent_centers(
        self,
        camera_id: str,
        *,
        since: datetime,
        until: datetime,
    ) -> list[DetectionSample]:
        self.calls.append((camera_id, since, until))
        return [sample for sample in self.samples if since <= sample.captured_at <= until]
