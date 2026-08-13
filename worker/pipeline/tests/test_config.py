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
