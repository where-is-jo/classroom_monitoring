"""진입점의 설정 오류 처리 검증."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ..config import StreamSettings
from ..main import _format_validation_error


def capture_validation_error(**values: object) -> ValidationError:
    with pytest.raises(ValidationError) as error:
        StreamSettings(**values)  # type: ignore[arg-type]
    return error.value


def test_없는_변수의_이름을_환경변수_표기로_알린다() -> None:
    message = _format_validation_error(capture_validation_error())

    assert "APP_ENV" in message
    assert "STREAM_SOURCES" in message


def test_오류_메시지에_설정값을_넣지_않는다() -> None:
    """STREAM_SOURCES에는 카메라 자격 증명이 들어갈 수 있다."""
    error = capture_validation_error(
        app_env="prod",
        stream_sources="camera-01=rtsp://admin:SuperSecret123@10.0.0.5:8554/cam",
        recording_enabled=True,
    )

    message = _format_validation_error(error)

    assert "SuperSecret123" not in message
    assert "admin" not in message
    assert "RECORDING_ENABLED" in message


def test_모델_수준_오류도_사유를_남긴다() -> None:
    error = capture_validation_error(
        app_env="local",
        stream_sources="camera-01=rtsp://host/1",
        rtsp_publish_enabled=True,
    )

    message = _format_validation_error(error)

    assert "RTSP_PUBLISH_DEVICE_NAME" in message
