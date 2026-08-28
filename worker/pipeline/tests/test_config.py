"""PipelineSettings 검증. 파이프라인 조립 값이 올바르게 읽히는지 본다."""

from __future__ import annotations

import pytest

from ..config import PipelineSettings


def build_settings(**overrides: object) -> PipelineSettings:
    # _env_file=None으로 pipeline/.env를 무시한다. 개발자가 로컬 .env를 두면
    # 그 값이 기본값 검증을 덮어써서 테스트가 사람마다 다르게 통과한다.
    return PipelineSettings(_env_file=None, **overrides)  # type: ignore[arg-type]


def test_fastapi_url_기본값은_127_0_0_1_8001이다() -> None:
    settings = build_settings()

    assert settings.fastapi_url == "http://127.0.0.1:8001"


def test_fastapi_url_을_지정하면_그대로_쓴다() -> None:
    settings = build_settings(fastapi_url="http://localhost:9000")

    assert settings.fastapi_url == "http://localhost:9000"


def test_fastapi_url_은_문자열로_로드한다() -> None:
    settings = build_settings(fastapi_url="http://10.0.0.5:8001")

    assert settings.fastapi_url == "http://10.0.0.5:8001"
    assert isinstance(settings.fastapi_url, str)


def test_신원_인계는_얼굴_서비스가_확정한_학생을_다시_탈락시키지_않는다() -> None:
    settings = build_settings()

    assert settings.identity_handover_min_confidence == 0.0


def test_입구와_CCTV는_역할별_샘플링_주기를_쓴다() -> None:
    settings = build_settings()

    # 얼굴 모델을 GPU로 옮긴 뒤 입구를 20에서 4로 올렸다(#181·#183). CPU 시절의 20은
    # 초당 0.74장이었고 그것이 입구 화면 갱신 주기의 상한이었다.
    assert settings.face_identity_sample_interval_frames == 4
    assert settings.person_tracking_sample_interval_frames == 4


def test_사람_후처리는_기존_동작이_기본이고_임계값을_고정한다() -> None:
    settings = build_settings()

    assert settings.person_detection_postprocess_enabled is False
    assert settings.person_detection_duplicate_iou_threshold == 0.5
    assert settings.person_detection_duplicate_ios_threshold == 0.85


def test_사람_후처리는_환경변수로_활성화할_수_있다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PERSON_DETECTION_POSTPROCESS_ENABLED", "true")

    settings = build_settings()

    assert settings.person_detection_postprocess_enabled is True


def test_역할별_샘플링_주기는_환경변수로_재정의할_수_있다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FACE_IDENTITY_SAMPLE_INTERVAL_FRAMES", "16")
    monkeypatch.setenv("PERSON_TRACKING_SAMPLE_INTERVAL_FRAMES", "3")

    settings = build_settings()

    assert settings.face_identity_sample_interval_frames == 16
    assert settings.person_tracking_sample_interval_frames == 3


def test_지표_노출_기본값은_켜짐이다() -> None:
    """저장 기능과 달리 개인정보가 나가지 않아 기본으로 켠다."""
    settings = build_settings()

    assert settings.metrics_enabled is True
    assert settings.metrics_port == 9101


def test_지표_바인딩_주소_기본값은_모든_인터페이스다() -> None:
    """컨테이너 밖의 Prometheus가 붙어야 해서 0.0.0.0이다."""
    settings = build_settings()

    assert settings.metrics_host == "0.0.0.0"


def test_지표_노출을_끌_수_있다() -> None:
    settings = build_settings(metrics_enabled=False)

    assert settings.metrics_enabled is False


def test_지표_바인딩_주소를_로컬로_낮출_수_있다() -> None:
    settings = build_settings(metrics_host="127.0.0.1", metrics_port=19101)

    assert settings.metrics_host == "127.0.0.1"
    assert settings.metrics_port == 19101


def test_신원_인계_route를_파싱한다() -> None:
    settings = build_settings(
        face_identity_url="http://deeplearning:8100",
        face_identity_camera_ids="entry-camera",
        identity_handover_routes=(
            '[{"entry_camera_id":"entry-camera",'
            '"classroom_camera_id":"classroom-cctv",'
            '"classroom_entry_zone":[0,0,0.3,1]}]'
        ),
    )

    route = settings.parsed_identity_handover_routes[0]
    assert route.entry_camera_id == "entry-camera"
    assert route.classroom_camera_id == "classroom-cctv"


def test_인계_route의_입구_카메라는_얼굴_식별_대상이어야_한다() -> None:
    with pytest.raises(ValueError, match="FACE_IDENTITY_CAMERA_IDS"):
        build_settings(
            face_identity_url="http://deeplearning:8100",
            face_identity_camera_ids="different-entry",
            identity_handover_routes=(
                '[{"entry_camera_id":"entry-camera",'
                '"classroom_camera_id":"classroom-cctv",'
                '"classroom_entry_zone":[0,0,0.3,1]}]'
            ),
        )


def test_얼굴_전용과_사람_추적_카메라는_겹칠_수_없다() -> None:
    with pytest.raises(ValueError, match="겹칠 수 없습니다"):
        build_settings(
            face_identity_url="http://deeplearning:8100",
            face_identity_camera_ids="entry-camera",
            person_tracking_camera_ids="entry-camera,classroom-cctv",
        )


def test_인계_route의_교실_카메라는_사람_추적_대상이어야_한다() -> None:
    with pytest.raises(ValueError, match="교실 카메라"):
        build_settings(
            face_identity_url="http://deeplearning:8100",
            face_identity_camera_ids="entry-camera",
            person_tracking_camera_ids="different-cctv",
            identity_handover_routes=(
                '[{"entry_camera_id":"entry-camera",'
                '"classroom_camera_id":"classroom-cctv",'
                '"classroom_entry_zone":[0,0,0.3,1]}]'
            ),
        )


def test_신원_인계를_켜고_ByteTrack을_끌_수_없다() -> None:
    with pytest.raises(ValueError, match="PERSON_TRACKING_ENABLED"):
        build_settings(
            person_tracking_enabled=False,
            face_identity_url="http://deeplearning:8100",
            face_identity_camera_ids="entry-camera",
            identity_handover_routes=(
                '[{"entry_camera_id":"entry-camera",'
                '"classroom_camera_id":"classroom-cctv",'
                '"classroom_entry_zone":[0,0,0.3,1]}]'
            ),
        )


def test_인계_track_stale은_시간창과_시각오차의_합보다_길어야_한다() -> None:
    with pytest.raises(ValueError, match="CLOCK_SKEW"):
        build_settings(
            face_identity_url="http://deeplearning:8100",
            face_identity_camera_ids="entry-camera",
            identity_handover_routes=(
                '[{"entry_camera_id":"entry-camera",'
                '"classroom_camera_id":"classroom-cctv",'
                '"classroom_entry_zone":[0,0,0.3,1]}]'
            ),
            identity_handover_max_delay_seconds=8,
            identity_handover_clock_skew_seconds=2,
            identity_handover_track_stale_seconds=9,
        )
