"""PipelineSettings 검증. 파이프라인 조립 값이 올바르게 읽히는지 본다."""

from __future__ import annotations

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
