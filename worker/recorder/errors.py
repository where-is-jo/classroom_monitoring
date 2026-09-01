"""recorder worker의 도메인 예외."""

from __future__ import annotations


class RecorderError(Exception):
    """recorder worker에서 발생하는 모든 오류의 상위 예외."""


class SegmentationError(RecorderError):
    """RTSP를 세그먼트 파일로 떨구는 FFmpeg 프로세스를 띄우지 못했다."""


# ObjectStorageError는 여기 없다. 객체 저장소를 inference도 쓰게 되면서
# shared/object_storage/errors.py로 옮겼다(결정 0011).
# **RecorderError를 더 이상 상속하지 않는다.** RecorderError만 잡는 곳은
# ObjectStorageError를 함께 잡아야 한다 — main.py가 그렇게 되어 있다.
