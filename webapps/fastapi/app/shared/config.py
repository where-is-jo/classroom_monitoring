"""애플리케이션 설정.

값은 환경변수로 주입한다. 규칙은 docs/conventions/environment-convention.md에 있다.

필수 환경변수는 기본값 없이 선언한다. 그러면 값이 없을 때 프로세스가 기동하면서
바로 실패한다. 요청을 처리하다가 설정이 없어서 실패하는 상황을 만들지 않기 위해서다.

현재는 인메모리 어댑터만 쓰므로 필수값이 없다. MongoDB·MinIO 어댑터를 붙이면
`database_url` 같은 항목을 기본값 없이 여기에 추가한다.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"

    # 신뢰도 판정 임계값. 화면과 API가 아니라 서비스 계층에서 적용한다.
    high_confidence_threshold: float = 0.80
    medium_confidence_threshold: float = 0.50

    # 페이지네이션. 상한을 두지 않으면 전체 조회 요청이 들어온다.
    page_size_default: int = 50
    page_size_max: int = 200
