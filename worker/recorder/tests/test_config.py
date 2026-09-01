"""설정 검증. 합의되지 않은 정책으로 조용히 뜨지 않는지 본다."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ..config import DEFAULT_DATA_DIR, RecorderSettings

VALID_ENV = {
    "app_env": "local",
    "stream_sources": "camera-01=rtsp://localhost:8554/camera",
}


def build_settings(**overrides: object) -> RecorderSettings:
    # _env_file=None으로 recorder/.env를 무시한다. 개발자가 로컬 .env를 두면
    # 그 값이 기본값 검증을 덮어써서 테스트가 사람마다 다르게 통과한다.
    return RecorderSettings(_env_file=None, **{**VALID_ENV, **overrides})  # type: ignore[arg-type]


def test_소스_목록을_stream과_같은_형식으로_읽는다() -> None:
    settings = build_settings(
        stream_sources="camera-01=rtsp://host/1,camera-02=rtsp://host/2"
    )

    assert [source.camera_id for source in settings.camera_sources] == [
        "camera-01",
        "camera-02",
    ]


def test_잘못된_소스_형식은_시작_시점에_걸린다() -> None:
    with pytest.raises(ValidationError):
        build_settings(stream_sources="형식이-틀린-값")


def test_기본_저장소는_로컬이다() -> None:
    settings = build_settings()

    assert settings.object_storage_backend == "local"


def test_prod에서는_로컬_저장소를_쓸_수_없다() -> None:
    """로컬 디렉터리는 보존 기간·접근 권한을 분리할 수 없다(결정 0004)."""
    with pytest.raises(ValidationError, match="OBJECT_STORAGE_BACKEND=local"):
        build_settings(app_env="prod", object_storage_backend="local")


def test_minio를_고르면_접속_정보가_필수다() -> None:
    with pytest.raises(ValidationError) as error:
        build_settings(object_storage_backend="minio")

    message = str(error.value)
    assert "OBJECT_STORAGE_ENDPOINT" in message
    assert "OBJECT_STORAGE_ACCESS_KEY" in message
    assert "OBJECT_STORAGE_SECRET_KEY" in message


def test_minio_접속_정보가_있으면_통과한다() -> None:
    settings = build_settings(
        app_env="prod",
        object_storage_backend="minio",
        object_storage_endpoint="minio.internal:9000",
        object_storage_access_key="key",
        object_storage_secret_key="secret",
    )

    assert settings.object_storage_backend == "minio"


def test_stale_시간이_세그먼트_길이보다_짧으면_거부한다() -> None:
    """짧으면 아직 쓰는 중인 세그먼트를 완료로 보고 올리게 된다."""
    with pytest.raises(ValidationError, match="RECORDING_STALE_AFTER_SECONDS"):
        build_settings(
            recording_segment_seconds=600, recording_stale_after_seconds=300
        )


def test_보존_기간_기본값은_30일이다() -> None:
    """팀 합의값이 아니다. 값이 바뀌면 문서도 함께 고쳐야 한다."""
    assert build_settings().recording_retention_days == 30


def test_보존_기간은_1일_미만이_될_수_없다() -> None:
    with pytest.raises(ValidationError):
        build_settings(recording_retention_days=0)


def test_저장_경로_기본값은_실행_위치가_아니라_recorder_아래다() -> None:
    settings = build_settings()

    assert settings.recording_segment_dir == DEFAULT_DATA_DIR / "segments"
    assert settings.object_storage_local_dir == DEFAULT_DATA_DIR / "objects"
    assert settings.recording_segment_dir.is_absolute()


def test_빈_경로_문자열은_기본값을_쓴다() -> None:
    settings = build_settings(recording_segment_dir="  ", object_storage_local_dir="")

    assert settings.recording_segment_dir == DEFAULT_DATA_DIR / "segments"
    assert settings.object_storage_local_dir == DEFAULT_DATA_DIR / "objects"


def test_경로를_지정하면_그대로_쓴다(tmp_path: Path) -> None:
    settings = build_settings(recording_segment_dir=str(tmp_path))

    assert settings.recording_segment_dir == tmp_path


def test_설정을_문자열로_찍어도_비밀값이_노출되지_않는다() -> None:
    settings = build_settings(
        stream_sources="camera-01=rtsp://admin:SuperSecret@host:8554/c1",
        object_storage_backend="minio",
        object_storage_endpoint="minio.internal:9000",
        object_storage_access_key="AKIAEXAMPLE",
        object_storage_secret_key="TopSecretKey",
    )

    rendered = str(settings)
    assert "SuperSecret" not in rendered
    assert "TopSecretKey" not in rendered
    assert "AKIAEXAMPLE" not in rendered


def test_잘못된_버킷_이름을_시작_시점에_거부한다() -> None:
    """SDK는 첫 호출 때 ValueError를 던진다. 그때는 이미 세그먼트가 쌓이는 중이다."""
    with pytest.raises(ValidationError, match="버킷 이름은"):
        build_settings(object_storage_bucket="b")


@pytest.mark.parametrize("name", ["Office-Recordings", "office_recordings", "-office", "office-"])
def test_S3_규칙에_어긋나는_버킷_이름을_거부한다(name: str) -> None:
    with pytest.raises(ValidationError, match="버킷 이름은"):
        build_settings(object_storage_bucket=name)


def test_유효한_버킷_이름은_통과한다() -> None:
    assert build_settings(object_storage_bucket="office-recordings-2026").object_storage_bucket
