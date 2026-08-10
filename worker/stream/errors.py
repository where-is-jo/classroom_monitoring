"""stream worker의 도메인 예외.

호출자가 "무엇이 실패했는지"로 분기할 수 있어야 해서 표준 예외 대신 따로 정의한다.
"""

from __future__ import annotations


class StreamWorkerError(Exception):
    """stream worker에서 발생하는 모든 오류의 상위 예외."""


class CameraConnectionError(StreamWorkerError):
    """카메라 연결 수립 또는 유지에 실패했다."""


class RtspPublishError(StreamWorkerError):
    """USB 카메라를 RTSP로 송출하는 FFmpeg 프로세스를 띄우지 못했다."""
