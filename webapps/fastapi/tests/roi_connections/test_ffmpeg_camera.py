"""RTSP 캡처 어댑터의 설정 해석과 실패 처리."""

from __future__ import annotations

import pytest

from app.roi_connections.adapters.ffmpeg_camera import (
    FfmpegCameraFrameGrabber,
    UnavailableCameraFrameGrabber,
    mask_url_credentials,
    parse_camera_sources,
)
from app.roi_connections.errors import CameraFrameUnavailableError


def test_sources_are_parsed_in_the_same_format_as_the_worker() -> None:
    sources = parse_camera_sources(
        "camera-01=rtsp://user:pw@10.0.0.1:554/stream, camera-02=rtsp://10.0.0.2:554/s2"
    )

    assert set(sources) == {"camera-01", "camera-02"}
    assert sources["camera-01"].rtsp_url == "rtsp://user:pw@10.0.0.1:554/stream"


@pytest.mark.parametrize(
    "raw",
    [
        "camera-01",  # 구분자가 없다
        "Camera_01=rtsp://10.0.0.1/s",  # 대문자와 밑줄은 쓰지 않는다
        "camera-01=http://10.0.0.1/s",  # rtsp가 아니다
        "camera-01=rtsp://a/s,camera-01=rtsp://b/s",  # 식별자가 겹친다
    ],
)
def test_malformed_sources_are_rejected(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_camera_sources(raw)


def test_credentials_never_appear_in_log_text() -> None:
    """카메라 접속 정보는 비밀값이라 로그·오류 문구에 원본을 남기지 않는다."""
    masked = mask_url_credentials("rtsp://operator:s3cret@10.0.0.7:554/rtsp/streaming?channel=01")

    assert "s3cret" not in masked
    assert "operator" not in masked
    # host는 남긴다. 어느 카메라가 실패했는지 로그에서 알아야 한다.
    assert "10.0.0.7:554" in masked


def test_unknown_camera_is_reported_as_unavailable() -> None:
    grabber = FfmpegCameraFrameGrabber(
        parse_camera_sources("camera-01=rtsp://10.0.0.1:554/s"), timeout_seconds=1
    )

    assert grabber.is_available("camera-01") is True
    assert grabber.is_available("camera-99") is False
    with pytest.raises(CameraFrameUnavailableError):
        grabber.capture_jpeg("camera-99")


def test_missing_ffmpeg_binary_is_reported_not_crashed() -> None:
    """ffmpeg가 없는 환경에서도 앱이 죽지 않고 원인을 알리는 오류로 끝난다."""
    grabber = FfmpegCameraFrameGrabber(
        parse_camera_sources("camera-01=rtsp://10.0.0.1:554/s"),
        timeout_seconds=1,
        ffmpeg_path="ffmpeg-that-does-not-exist",
    )

    with pytest.raises(CameraFrameUnavailableError) as raised:
        grabber.capture_jpeg("camera-01")
    assert "ffmpeg" in str(raised.value)


def test_unset_sources_disable_capture_only() -> None:
    grabber = UnavailableCameraFrameGrabber()

    assert grabber.is_available("camera-01") is False
    with pytest.raises(CameraFrameUnavailableError):
        grabber.capture_jpeg("camera-01")
