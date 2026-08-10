"""recorder worker의 도메인 예외."""

from __future__ import annotations


class RecorderError(Exception):
    """recorder worker에서 발생하는 모든 오류의 상위 예외."""


class SegmentationError(RecorderError):
    """RTSP를 세그먼트 파일로 떨구는 FFmpeg 프로세스를 띄우지 못했다."""


class ObjectStorageError(RecorderError):
    """객체 저장소에 적재하거나 삭제하지 못했다."""
