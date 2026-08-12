"""객체 저장소 경계의 예외.

`recorder`의 `RecorderError` 아래에 두지 않는다. 여기가 `shared`이므로 특정 워커의
예외 계층을 알 수 없다. 그래서 이 예외는 어느 워커에도 속하지 않는 독립 예외다.

**워커 진입점이 `RecorderError`만 잡고 있었다면 함께 잡도록 고쳐야 한다.**
상속 관계가 끊어졌기 때문이다.
"""

from __future__ import annotations


class ObjectStorageError(Exception):
    """객체 저장소에 적재하거나 조회하거나 삭제하지 못했다."""
