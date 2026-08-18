"""LLM에게 줄 지시문을 만든다. 순수 함수다.

어댑터 안에 문자열을 인라인하지 않는 이유는 **계약 문구를 테스트로 검증하기
위해서다.** 프롬프트와 `planning.py`의 검증 규칙은 한 쌍이라, 한쪽만 바뀌면
모델이 규격에 맞는 답을 내도 422가 된다.

시각대를 여기서 다룬다. 사용자는 "오늘 6시"처럼 한국 시각으로 말하고 저장소는
UTC로 조회한다. **그 변환을 모델에게 시키지 않는다.**

## 왜 없는 강의실을 null로 만들지 않는가

이전 지시문은 "목록에 없는 곳을 말했으면 null"이었다. 그러면 **"B동 305호"를 물은
사람과 아무 곳도 말하지 않은 사람이 서버에서 같아진다.** null은 `service.py`에서
"전체 카메라"로 해석되므로, 없는 강의실을 물은 사용자는 안내 대신 엉뚱한 전체
결과를 받는다. 등록되지 않았다는 사실을 알려 줄 자리가 사라지는 것이다.

그래서 **들은 이름을 그대로 옮기게 하고, 목록과 대조하는 일은 서버가 한다.**
`_resolve_targets`가 이미 그 판정과 안내 문구를 갖고 있다. 모델이 옮겨 적은 값은
`planning.py`의 문자열 검증과 길이 상한을 거치고, 저장소 조회도 문자열 동등 비교라
그대로 흘러가도 위험하지 않다.

"식별자를 지어내지 마라"와 다른 요구다. 지어내기는 **목록에 있는 값을 임의로 바꾸는
것**이고, 여기서 요구하는 것은 들은 말을 옮기는 것이다.

## 왜 모델에게 9시간을 빼게 하지 않는가

이전 지시문은 "KST에서 9시간을 빼서 UTC로 적어라"였다. 작은 모델이 가장 자주 틀리는
종류의 작업이고, 특히 자정을 넘길 때(06:00 KST → 전날 21:00 UTC) 날짜를 그대로 두는
실패가 흔하다. 더 나쁜 것은 **검증이 이 실패를 잡지 못한다는 점이다.** 9시간 어긋난
값도 형식상 완벽한 ISO 8601이라 `planning.py`를 그대로 통과하고, 사용자는 조용히
빈 결과를 받는다.

그래서 모델에게는 **들은 시각을 그대로 적고 `+09:00`만 붙이라고** 요구한다. 산술이
`datetime.astimezone`으로 옮겨 가고, 모델이 하는 일은 "6시"를 알아보는 것까지로
줄어든다. 검증은 바뀌지 않는다 — `_required_datetime`이 이미 어떤 오프셋이든 UTC로
정규화하므로, 모델이 굳이 UTC로 답해도 정답이면 통과한다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from zoneinfo import ZoneInfo

from .models import CameraChoice

# app/video_monitoring/service.py와 같은 값을 쓴다. 이 서비스가 다루는 강의실이
# 한국에 있다는 사실은 설정이 아니라 전제다.
KST = ZoneInfo("Asia/Seoul")

_INSTRUCTION = """\
너는 강의실 모니터링 시스템의 검색 조건 변환기다.
사용자의 한국어 질문을 읽고 **JSON 객체 하나만** 출력한다.

설명, 인사, 코드펜스(```), 여는 괄호 앞뒤의 어떤 글자도 쓰지 마라.

출력 형식:
{{"intent":"detection_search","camera_id":null,"classroom_id":null,"from":"...","to":"...","limit":20}}

규칙:
- intent는 항상 "detection_search"다. 다른 값을 쓰지 마라.
- from, to는 **사용자가 말한 시각을 그대로 적고 뒤에 +09:00을 붙인다.**
  예) "6시" -> "{today_kst}T06:00:00+09:00"
- **시각을 계산하지 마라.** 빼지도 더하지도 말고 들은 그대로 적는다.
- from은 to보다 앞서야 한다. 같으면 안 된다.
- to는 구간의 끝이며 그 시각 자체는 포함되지 않는다. "6시부터 7시 사이"는 06:00~07:00이다.
- 기간을 말하지 않았으면 오늘 하루(00:00부터 다음날 00:00까지)로 잡아라.
- limit은 1 이상 {max_limit} 이하의 정수다. 말하지 않았으면 {max_limit}을 쓴다.
- 위 여섯 개 말고 다른 키를 넣지 마라.

강의실이나 카메라를 말했으면 아래 목록의 식별자를 그대로 쓴다.
**목록에 없는 곳을 말했으면 사용자가 말한 이름을 그대로 옮겨 적는다.**
없는 곳이라고 판단해 null로 바꾸지 마라 — 등록되지 않았다는 안내는 서버가 한다.
**아무 곳도 말하지 않았을 때만 null이다.**
목록에 있는 식별자를 임의로 바꾸거나 섞지 마라. 목록에 없는 식별자를 지어내지 마라.
{camera_lines}

지금 시각: {now_kst}
오늘 날짜: {today_kst}\
"""


_RETRY_SUFFIX = """

**직전 응답이 규격을 벗어났다.** 이번에는 JSON 객체 하나만, 위에 적힌 키만 써서
출력하라. 설명도 코드펜스도 붙이지 마라."""


def build_system_prompt(
    *,
    now: datetime,
    cameras: Sequence[CameraChoice],
    max_limit: int,
    retry: bool = False,
) -> str:
    """모델에게 줄 지시문을 만든다.

    `now`는 호출자가 한 번만 구해서 넘긴다. 프롬프트의 "지금"과 검증의 "지금"이
    다른 값이면 경계 시각에서 결과가 어긋난다.

    **"지금"을 한국 시각으로만 알려준다.** UTC를 함께 주면 모델이 그쪽으로 변환하려
    들고, 그 변환이 이 기능에서 없애려는 실패 그 자체다.

    `max_limit`은 호출자가 요청한 상한과 같은 값이어야 한다. 지시문이 허용한 수를
    검증이 되돌려 깎으면, 모델은 규격을 지켰는데 결과가 줄어든 것처럼 보인다.

    `retry`는 직전 응답이 규격을 벗어났을 때 켠다. **모델이 뱉은 원문은 넣지 않는다.**
    되돌려 넣어 봐야 같은 실수를 다시 읽게 만들 뿐이고, 프롬프트를 모델 출력으로
    오염시키는 경로가 된다. 규격을 다시 못 박는 문장 하나면 족하다.
    """
    now_kst = now.astimezone(KST)
    instruction = _INSTRUCTION.format(
        max_limit=max_limit,
        camera_lines=_format_cameras(cameras),
        now_kst=now_kst.strftime("%Y-%m-%d %H:%M:%S"),
        today_kst=now_kst.strftime("%Y-%m-%d"),
    )
    return instruction + _RETRY_SUFFIX if retry else instruction


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
