"""설정 검증 오류를 안전하게 사람이 읽을 형태로 바꾼다."""

from __future__ import annotations

from pydantic import ValidationError


def format_validation_error(error: ValidationError) -> str:
    """설정 오류를 변수 이름과 사유만으로 정리한다.

    Pydantic 기본 출력은 입력값을 그대로 붙인다. STREAM_SOURCES처럼 카메라 자격
    증명이 담길 수 있는 값이 로그에 남으면 안 되므로 입력값을 뺀다.
    """
    lines = []
    for item in error.errors(include_url=False, include_input=False):
        location = ".".join(str(part) for part in item["loc"])
        # 설정은 환경변수에서 오므로 이름을 환경변수 표기로 보여 준다.
        name = location.upper() if location else "(설정 전체)"
        lines.append(f"  - {name}: {item['msg']}")
    return "\n".join(lines)
