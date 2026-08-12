"""객체 키 규칙.

결정 0004가 "버킷 구성과 객체 키 규칙 정의"를 남은 일로 두었다. 여기서 정한다.

```text
<카메라 식별자>/<YYYY-MM-DD>/<YYYYMMDDTHHMMSSZ>.mp4
예) camera-01/2026-08-10/20260810T090000Z.mp4
```

- **카메라를 맨 앞에 둔다.** 카메라 단위로 접근 권한을 나누거나 통째로 지우는 일이
  기간 단위보다 잦다.
- **날짜 디렉터리를 둔다.** 보존 기간 삭제와 특정 날짜 조회가 접두사 하나로 끝난다.
- **시각은 UTC ISO 8601 기본형이다.** 콜론은 일부 파일 시스템과 S3 도구에서 다루기
  번거로워 기본형(`T`, `Z`, 구분자 없음)을 쓴다. API 규칙의 확장형과 다른 것은
  객체 키가 API 응답 필드가 아니기 때문이다.
- **시각대를 키에 담는다.** 로컬 시각으로 두면 서버 시각대가 바뀔 때 같은 순간의
  객체가 두 날짜에 걸친다.
"""

from __future__ import annotations

from datetime import UTC, datetime

_KEY_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"
_KEY_DATE_FORMAT = "%Y-%m-%d"


def build_object_key(camera_id: str, recorded_at: datetime, *, suffix: str = ".mp4") -> str:
    """세그먼트 하나의 객체 키를 만든다."""
    moment = recorded_at.astimezone(UTC)
    return (
        f"{camera_id}/"
        f"{moment.strftime(_KEY_DATE_FORMAT)}/"
        f"{moment.strftime(_KEY_TIMESTAMP_FORMAT)}{suffix}"
    )


def camera_prefix(camera_id: str) -> str:
    """한 카메라의 모든 객체를 가리키는 접두사."""
    return f"{camera_id}/"
