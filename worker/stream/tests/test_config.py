"""설정 파싱과 시작 시 검증. 잘못된 값으로 조용히 뜨지 않는지 본다."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ..config import (
    DEFAULT_DATA_DIR,
    StreamSettings,
    mask_url_credentials,
    parse_stream_sources,
)

VALID_ENV = {
    "app_env": "local",
    "stream_sources": "camera-01=rtsp://localhost:8554/camera",
}


def build_settings(**overrides: object) -> StreamSettings:
    # _env_file=None으로 stream/.env.*를 무시한다. 개발자가 로컬 .env.local을 두면
    # 그 값이 기본값 검증을 덮어써서 테스트가 사람마다 다르게 통과한다.
    return StreamSettings(_env_file=None, **{**VALID_ENV, **overrides})  # type: ignore[arg-type]


class TestParseStreamSources:
    def test_여러_소스를_식별자와_함께_읽는다(self) -> None:
        sources = parse_stream_sources(
            "camera-01=rtsp://host:8554/c1, camera-02=rtsp://host:8554/c2"
        )

        assert [source.camera_id for source in sources] == ["camera-01", "camera-02"]
        assert sources[1].rtsp_url == "rtsp://host:8554/c2"

    def test_빈_목록은_거부한다(self) -> None:
        with pytest.raises(ValueError, match="영상 소스가 하나도 없습니다"):
            parse_stream_sources("  ,  ")

    def test_식별자가_없으면_거부한다(self) -> None:
        with pytest.raises(ValueError, match="형식이어야 합니다"):
            parse_stream_sources("rtsp://host:8554/c1")

    def test_중복된_식별자를_거부한다(self) -> None:
        with pytest.raises(ValueError, match="중복된 카메라 식별자"):
            parse_stream_sources("cam=rtsp://host/1,cam=rtsp://host/2")

    def test_rtsp가_아닌_스킴을_거부한다(self) -> None:
        with pytest.raises(ValueError, match="rtsp:// 로 시작하지 않습니다"):
            parse_stream_sources("cam=http://host/1")

    def test_잘못된_스킴_오류에_URL_원문을_넣지_않는다(self) -> None:
        with pytest.raises(ValueError) as error:
            parse_stream_sources("cam=http://admin:secret@host/1")

        assert "secret" not in str(error.value)

    def test_대문자_식별자를_거부한다(self) -> None:
        with pytest.raises(ValueError, match="소문자·숫자·하이픈"):
            parse_stream_sources("Camera_01=rtsp://host/1")


class TestMaskUrlCredentials:
    def test_자격_증명을_지운다(self) -> None:
        assert (
            mask_url_credentials("rtsp://admin:secret@10.0.0.5:8554/camera")
            == "rtsp://***@10.0.0.5:8554/camera"
        )

    def test_자격_증명이_없으면_그대로_둔다(self) -> None:
        assert (
            mask_url_credentials("rtsp://localhost:8554/camera")
            == "rtsp://localhost:8554/camera"
        )


class TestStreamSettings:
    def test_기본값으로_저장_기능이_꺼져_있다(self) -> None:
        settings = build_settings()

        assert settings.recording_enabled is False
        assert settings.frame_capture_enabled is False
        assert settings.rtsp_publish_enabled is False

    def test_소스_목록을_객체로_돌려준다(self) -> None:
        settings = build_settings(
            stream_sources="camera-01=rtsp://host/1,camera-02=rtsp://host/2"
        )

        assert len(settings.camera_sources) == 2

    def test_잘못된_소스_형식은_시작_시점에_걸린다(self) -> None:
        with pytest.raises(ValidationError):
            build_settings(stream_sources="형식이-틀린-값")

    def test_prod에서는_영상_저장을_켤_수_없다(self) -> None:
        with pytest.raises(ValidationError, match="RECORDING_ENABLED"):
            build_settings(app_env="prod", recording_enabled=True)

    def test_prod에서는_프레임_저장을_켤_수_없다(self) -> None:
        with pytest.raises(ValidationError, match="FRAME_CAPTURE_ENABLED"):
            build_settings(app_env="prod", frame_capture_enabled=True)

    def test_local에서는_저장을_켤_수_있다(self) -> None:
        settings = build_settings(recording_enabled=True, frame_capture_enabled=True)

        assert settings.recording_enabled is True

    def test_송출을_켜면_장치와_대상_URL이_필수다(self) -> None:
        with pytest.raises(ValidationError) as error:
            build_settings(rtsp_publish_enabled=True)

        message = str(error.value)
        assert "RTSP_PUBLISH_DEVICE_NAME" in message
        assert "RTSP_PUBLISH_TARGET_URL" in message

    def test_저장_경로_기본값은_실행_위치가_아니라_stream_아래다(self) -> None:
        settings = build_settings()

        assert settings.recording_output_dir == DEFAULT_DATA_DIR / "video"
        assert settings.recording_output_dir.is_absolute()

    def test_빈_경로_문자열은_기본값을_쓴다(self) -> None:
        settings = build_settings(recording_output_dir="  ")

        assert settings.recording_output_dir == DEFAULT_DATA_DIR / "video"

    def test_경로를_지정하면_그대로_쓴다(self, tmp_path: Path) -> None:
        settings = build_settings(recording_output_dir=str(tmp_path))

        assert settings.recording_output_dir == tmp_path

    def test_샘플링_주기는_1_미만이_될_수_없다(self) -> None:
        with pytest.raises(ValidationError):
            build_settings(frame_sample_interval_frames=0)

    def test_설정을_문자열로_찍어도_소스가_노출되지_않는다(self) -> None:
        settings = build_settings(
            stream_sources="camera-01=rtsp://admin:secret@host:8554/c1"
        )

        assert "secret" not in str(settings)
