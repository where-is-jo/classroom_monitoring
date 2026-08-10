"""RTSP 소스 하나의 연결을 유지하고 프레임을 읽는다.

장치는 자주 끊긴다. 연결 실패를 예외적 상황이 아니라 운영 중 정상적으로 발생하는
상태로 다루기 위해 연결 상태를 명시적으로 들고 있는다.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from enum import Enum
from typing import Protocol

import cv2
import numpy as np
from numpy.typing import NDArray

from .config import CameraSource
from .errors import CameraConnectionError

logger = logging.getLogger(__name__)

Frame = NDArray[np.uint8]


class ConnectionState(str, Enum):
    """카메라 연결 상태. monitoring 지표와 fastapi 상태 조회가 이 값을 쓴다."""

    IDLE = "idle"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"
    STOPPED = "stopped"


class VideoCaptureLike(Protocol):
    """OpenCV VideoCapture 중 이 모듈이 실제로 쓰는 부분만 추린 것.

    프로세스 밖으로 나가는 I/O 경계라 여기에 이음매를 둔다.
    실제 카메라 없이 연결 상태 전이를 단위 테스트하기 위한 것이기도 하다.
    """

    def isOpened(self) -> bool: ...

    def read(self) -> tuple[bool, Frame | None]: ...

    def release(self) -> None: ...

    def set(self, prop_id: int, value: float) -> bool: ...


CaptureFactory = Callable[[str], VideoCaptureLike]


def _open_rtsp_capture(rtsp_url: str) -> VideoCaptureLike:
    capture: VideoCaptureLike = cv2.VideoCapture(rtsp_url)
    # 버퍼를 1로 두지 않으면 읽는 속도가 느릴 때 지연이 계속 쌓인다.
    # camera-guides.md의 "reader is too slow" 대응이다.
    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return capture


class CameraReader:
    """카메라 한 대의 연결·재연결·프레임 읽기를 담당한다."""

    def __init__(
        self,
        source: CameraSource,
        *,
        max_retry: int,
        reconnect_delay_seconds: float,
        read_failure_tolerance: int,
        capture_factory: CaptureFactory = _open_rtsp_capture,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._source = source
        self._max_retry = max_retry
        self._reconnect_delay_seconds = reconnect_delay_seconds
        self._read_failure_tolerance = read_failure_tolerance
        self._capture_factory = capture_factory
        self._sleep = sleep

        self._capture: VideoCaptureLike | None = None
        self._state = ConnectionState.IDLE
        self._consecutive_read_failures = 0

    @property
    def camera_id(self) -> str:
        return self._source.camera_id

    @property
    def state(self) -> ConnectionState:
        return self._state

    def connect(self) -> None:
        """연결될 때까지 재시도한다. 모두 실패하면 CameraConnectionError를 올린다."""
        self._state = ConnectionState.RECONNECTING

        for attempt in range(1, self._max_retry + 1):
            capture = self._capture_factory(self._source.rtsp_url)
            if capture.isOpened():
                self._capture = capture
                self._consecutive_read_failures = 0
                self._state = ConnectionState.CONNECTED
                logger.info(
                    "카메라 %s 연결됨 (%s)", self._source.camera_id, self._source.masked_url
                )
                return

            # 열리지 않은 capture도 해제한다. 재시도마다 쌓이면 핸들이 고갈된다.
            capture.release()
            logger.warning(
                "카메라 %s 연결 실패 %d/%d",
                self._source.camera_id,
                attempt,
                self._max_retry,
            )
            if attempt < self._max_retry:
                self._sleep(self._reconnect_delay_seconds)

        self._state = ConnectionState.FAILED
        raise CameraConnectionError(
            f"카메라 {self._source.camera_id} 연결에 "
            f"{self._max_retry}회 실패했습니다 ({self._source.masked_url})"
        )

    def read(self) -> Frame | None:
        """프레임을 한 장 읽는다.

        일시적인 실패는 None으로 알리고, 연속 실패가 허용치를 넘으면 재연결한다.
        재연결까지 실패하면 CameraConnectionError를 올린다. 호출자가 실패를
        모른 채 계속 도는 상황을 만들지 않기 위해서다.
        """
        if self._capture is None:
            raise CameraConnectionError(
                f"카메라 {self._source.camera_id}가 연결되지 않았습니다. connect()를 먼저 부릅니다."
            )

        is_read, frame = self._capture.read()
        if is_read and frame is not None:
            self._consecutive_read_failures = 0
            return frame

        self._consecutive_read_failures += 1
        if self._consecutive_read_failures < self._read_failure_tolerance:
            return None

        logger.warning(
            "카메라 %s에서 %d회 연속으로 프레임을 읽지 못해 재연결한다",
            self._source.camera_id,
            self._consecutive_read_failures,
        )
        self._release_capture()
        self.connect()
        return None

    def close(self) -> None:
        self._release_capture()
        self._state = ConnectionState.STOPPED

    def _release_capture(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None
