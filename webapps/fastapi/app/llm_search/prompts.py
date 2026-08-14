"""LLM에게 줄 지시문을 만든다. 순수 함수다.

어댑터 안에 문자열을 인라인하지 않는 이유는 **계약 문구를 테스트로 검증하기
위해서다.** 프롬프트와 `planning.py`의 검증 규칙은 한 쌍이라, 한쪽만 바뀌면
모델이 규격에 맞는 답을 내도 422가 된다.

시각대를 여기서 다룬다. 사용자는 "오늘 6시"처럼 한국 시각으로 말하고 저장소는
UTC로 조회하므로, **변환을 모델에게 맡기고 그 결과를 우리가 검증한다.**
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from zoneinfo import ZoneInfo

from .models import CameraChoice

# app/video_monitoring/service.py와 같은 값을 쓴다. 이 서비스가 다루는 강의실이
# 한국에 있다는 사실은 설정이 아니라 전제다.
_KST = ZoneInfo("Asia/Seoul")

_INSTRUCTION = """\
너는 강의실 모니터링 시스템의 검색 조건 변환기다.
사용자의 한국어 질문을 읽고 **JSON 객체 하나만** 출력한다.

설명, 인사, 코드펜스(```), 여는 괄호 앞뒤의 어떤 글자도 쓰지 마라.

출력 형식:
{{"intent":"detection_search","camera_id":null,"classroom_id":null,"from":"...","to":"...","limit":20}}

규칙:
- intent는 항상 "detection_search"다. 다른 값을 쓰지 마라.
- from, to는 UTC ISO 8601이고 반드시 Z로 끝난다. 예: "2026-08-14T06:00:00Z"
- 사용자는 한국 시각(KST, UTC+9)으로 말한다. **KST에서 9시간을 빼서 UTC로 적어라.**
- from은 to보다 앞서야 한다. 같으면 안 된다.
- to는 구간의 끝이며 그 시각 자체는 포함되지 않는다. "6시부터 7시 사이"는 06:00~07:00이다.
- 기간을 말하지 않았으면 오늘 하루(KST 00:00 ~ 다음날 00:00)로 잡아라.
- limit은 1 이상 {max_limit} 이하의 정수다. 말하지 않았으면 {default_limit}을 쓴다.
- 위 여섯 개 말고 다른 키를 넣지 마라.

강의실이나 카메라를 특정했으면 아래 목록의 식별자를 그대로 쓴다.
**목록에 없는 곳을 말했거나 특정하지 않았으면 null을 쓴다. 식별자를 지어내지 마라.**
{camera_lines}

지금 시각: {now_kst} (KST) = {now_utc} (UTC)
오늘 날짜: {today_kst} (KST)\
"""


def build_system_prompt(
    *,
    now: datetime,
    cameras: Sequence[CameraChoice],
    max_limit: int,
    default_limit: int,
) -> str:
    """모델에게 줄 지시문을 만든다.

    `now`는 호출자가 한 번만 구해서 넘긴다. 프롬프트의 "지금"과 검증의 "지금"이
    다른 값이면 경계 시각에서 결과가 어긋난다.
    """
    now_kst = now.astimezone(_KST)
    return _INSTRUCTION.format(
        max_limit=max_limit,
        default_limit=default_limit,
        camera_lines=_format_cameras(cameras),
        now_kst=now_kst.strftime("%Y-%m-%d %H:%M:%S"),
        now_utc=now.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
        today_kst=now_kst.strftime("%Y-%m-%d"),
    )


def _format_cameras(cameras: Sequence[CameraChoice]) -> str:
    if not cameras:
        # 등록된 카메라가 없을 때 빈 목록을 그대로 보여주면 모델이 빈칸을 채우려
        # 든다. 사용할 수 있는 값이 null뿐이라고 분명히 말한다.
        return "등록된 카메라가 없다. camera_id와 classroom_id는 반드시 null로 둔다."
    lines = [
        f"- camera_id={camera.camera_id} / classroom_id={camera.classroom_id} / {camera.label}"
        for camera in cameras
    ]
    return "\n".join(lines)
