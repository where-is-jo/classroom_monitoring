"""env·yaml 소스 우선순위를 워커마다 같게 만든다.

각 워커 설정은 세 곳에서 값을 받는다: 실제 OS 환경변수, `.env.{APP_ENV}` 파일,
`config/settings.yml`. 이 함수가 없으면 네 워커(stream·inference·recorder·pipeline)가
같은 `settings_customise_sources` 본문을 각자 반복해야 하고, 우선순위가 워커마다
갈릴 위험이 생긴다. `ObjectStorageSettings`를 shared에 둔 것과 같은 이유다.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, YamlConfigSettingsSource

__all__ = ["customise_sources_with_yaml"]


def customise_sources_with_yaml(
    settings_cls: type[BaseSettings],
    init_settings: PydanticBaseSettingsSource,
    env_settings: PydanticBaseSettingsSource,
    dotenv_settings: PydanticBaseSettingsSource,
    file_secret_settings: PydanticBaseSettingsSource,
) -> tuple[PydanticBaseSettingsSource, ...]:
    """실제 OS 환경변수 > `.env.{APP_ENV}` 파일 > `config/settings.yml` 순으로 읽는다.

    yml에 있는 값도 실제 OS 환경변수나 `.env.*`로 즉석에서 덮어쓸 수 있다 — 우선순위가
    거기 있기 때문이다. 반대로 yml에 없는 값(비밀값·환경 의존 설정)은 애초에 yml에
    넣지 않는다.
    """
    return (
        init_settings,
        env_settings,
        dotenv_settings,
        YamlConfigSettingsSource(settings_cls),
        file_secret_settings,
    )
