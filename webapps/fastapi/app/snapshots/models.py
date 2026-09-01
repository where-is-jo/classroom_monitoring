"""탐지 스냅샷 도메인 모델과 객체 키 해석.

## 객체 키 규칙이 두 곳에 있다

키를 만드는 쪽은 `worker/shared/object_keys.py`다. 여기서는 그 규칙을 **다시 해석한다.**
worker와 fastapi는 최상위 디렉터리가 달라 코드를 공유할 수 없고, 공용 패키지를
최상위에 새로 두는 것은 구조 제약에 걸린다.

**키 규칙이 바뀌면 두 곳을 함께 고쳐야 한다.** 규칙의 원본은 worker 쪽이다.

```text
<카메라 식별자>/<YYYY-MM-DD>/<YYYYMMDDTHHMMSSZ>.jpg
예) camera-01/2026-08-12/20260812T090000Z.jpg
```
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

_KEY_PATTERN = re.compile(
    r"^(?P<camera_id>[^/]+)/(?P<date>\d{4}-\d{2}-\d{2})/"
    r"(?P<timestamp>\d{8}T\d{6}Z)\.(?P<extension>jpg|jpeg)$"
)
_KEY_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"
_KEY_DATE_FORMAT = "%Y-%m-%d"
_KEY_SUFFIX = ".jpg"

__all__ = ["Snapshot", "SnapshotPage", "build_snapshot_key", "parse_snapshot_key"]


@dataclass(frozen=True)
class Snapshot:
    """저장소에 있는 스냅샷 한 장."""

    key: str
    camera_id: str
    captured_at: datetime
    size_bytes: int


@dataclass(frozen=True)
class SnapshotPage:
    items: list[Snapshot]
    total: int
    limit: int
    offset: int


def build_snapshot_key(camera_id: str, captured_at: datetime) -> str:
    """카메라와 촬영 시각으로 객체 키를 만든다. `parse_snapshot_key`의 역이다.

    worker가 스냅샷을 올릴 때 쓰는 시각(`captured.captured_at`)과 탐지 이벤트에
    담기는 시각이 **같은 값**이라, 이벤트만 있으면 스냅샷 키를 계산할 수 있다.
    근접 매칭이 필요 없는 이유다.

    계산한 키가 실제로 저장소에 있는지는 별개다. worker는 탐지 개수가 바뀔 때만,
    최소 간격을 두고 올린다. **존재 확인 없이 이미지를 걸면 깨진 이미지가 뜬다.**

    키에는 초 단위까지만 담기므로 같은 초의 두 장은 구분되지 않는다.
    """
    moment = captured_at.astimezone(UTC)
    return (
        f"{camera_id}/"
        f"{moment.strftime(_KEY_DATE_FORMAT)}/"
        f"{moment.strftime(_KEY_TIMESTAMP_FORMAT)}{_KEY_SUFFIX}"
    )


def parse_snapshot_key(key: str) -> tuple[str, datetime] | None:
    """객체 키에서 카메라와 촬영 시각을 꺼낸다. 규칙에 맞지 않으면 None.

    None을 돌려주는 이유는 버킷에 사람이 넣은 파일이나 다른 규칙의 객체가 섞여 있을 수
    있기 때문이다. 하나가 이상하다고 목록 전체가 실패하면 안 된다.
    """
    match = _KEY_PATTERN.match(key)
    if match is None:
        return None

    try:
        # 키의 Z가 UTC를 뜻한다. %z로 읽히지 않는 형식이라 파싱 뒤에 시각대를 붙인다.
        captured_at = datetime.strptime(match["timestamp"], _KEY_TIMESTAMP_FORMAT).replace(
            tzinfo=UTC
        )
    except ValueError:
        return None
    return match["camera_id"], captured_at
