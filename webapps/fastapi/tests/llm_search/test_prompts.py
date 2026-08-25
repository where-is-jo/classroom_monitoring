"""프롬프트와 검증 규칙은 한 쌍이다.

한쪽만 바뀌면 모델이 규격에 맞는 답을 내도 422가 되거나, 규격을 벗어난 답이
통과한다. 계약 문구가 프롬프트에 실제로 들어가는지 여기서 고정한다.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.llm_search.models import CameraChoice
from app.llm_search.planning import MAX_LIMIT
from app.llm_search.prompts import build_system_prompt

_NOW = datetime(2026, 8, 14, 0, 30, tzinfo=UTC)  # KST로는 같은 날 09:30


def _prompt(cameras: list[CameraChoice]) -> str:
    return build_system_prompt(now=_NOW, cameras=cameras, max_limit=MAX_LIMIT)


def test_현재_시각을_한국_시각으로만_알려준다() -> None:
    """UTC를 함께 주면 모델이 그쪽으로 변환하려 든다. 그 변환이 없애려는 실패다."""
    prompt = _prompt([])

    assert "2026-08-14 09:30:00" in prompt
    assert "2026-08-14T00:30:00Z" not in prompt


def test_시각대를_변환하지_말라고_지시한다() -> None:
    """9시간 빼기는 작은 모델이 자주 틀리고, 틀려도 검증이 잡지 못한다.

    형식상 완벽한 ISO 8601이라 그대로 통과하고 사용자는 조용히 빈 결과를 받는다.
    """
    prompt = _prompt([])

    assert "+09:00" in prompt
    assert "시각대를 변환하지 마라" in prompt


def test_오전을_밝히지_않은_시각은_오후로_읽으라고_지시한다() -> None:
    """한국어 "3시"에는 오전·오후가 담기지 않는다. **검증이 잡지 못하는 실패다.**

    03:00도 형식상 완벽한 시각이라 `planning.py`를 그대로 통과하고, 사용자는
    "그 시간에 아무도 없었다"는 답을 받는다(2026-08-23 실측). 강의실을 쓰는
    시간대가 낮이라 오후로 읽는 편이 거의 언제나 맞다.
    """
    prompt = _prompt([])

    assert "오후로 읽는다" in prompt
    # 예시의 날짜는 프롬프트의 "오늘"과 같아야 한다. 다른 날짜를 예로 들면
    # 모델이 그 날짜를 그대로 베낀다.
    assert "2026-08-14T15:00:00+09:00" in prompt
    # 오전을 밝혔을 때의 예시도 함께 준다. 한쪽만 주면 모델이 언제나 그쪽으로 쏠린다.
    assert "2026-08-14T03:00:00+09:00" in prompt


def test_강의실을_부르는_이름을_식별자와_함께_알려준다() -> None:
    """`classroom_id`는 UUID라 질문에 등장하지 않는다.

    코드와 이름이 같은 줄에 없으면 모델은 "A101"을 식별자로 옮길 근거가 없어,
    들은 이름을 그대로 `classroom_id`에 적는다. 그러면 등록된 강의실을 물어도
    등록되지 않은 곳으로 판정된다(2026-08-23 실측).
    """
    prompt = _prompt(
        [
            CameraChoice(
                camera_id="camera-01",
                classroom_id="room-a101",
                label="A101 앞문",
                classroom_code="A101",
                classroom_name="1강의실",
            )
        ]
    )

    assert "classroom_id=room-a101 (A101 1강의실)" in prompt
    assert "괄호 앞의 classroom_id를 쓴다" in prompt


def test_강의실_등록을_찾지_못하면_식별자만_알려준다() -> None:
    """스트림에는 `classroom_id`만 담겨 있어 강의실 등록이 지워져도 스트림은 남는다."""
    prompt = _prompt(
        [
            CameraChoice(
                camera_id="camera-01",
                classroom_id="room-a101",
                label="A101 앞문",
                classroom_code=None,
                classroom_name=None,
            )
        ]
    )

    assert "classroom_id=room-a101 / A101 앞문" in prompt


def test_기간을_말하지_않았을_때의_두_날짜를_모두_알려준다() -> None:
    """ "다음날"이라고만 말하면 모델이 그 한 걸음을 건너뛴다.

    2026-08-23 실측: "A111 강의실에 오늘 몇 명 있었어?"에 from과 to를 둘 다 같은 날
    00:00으로 냈고, `EMPTY_RANGE`로 거절돼 사용자는 아무 잘못 없이 질문을 고치라는
    안내를 받았다. 날짜 계산은 이 파일의 다른 규칙과 마찬가지로 우리가 한다.
    """
    prompt = _prompt([])

    assert "2026-08-14T00:00:00+09:00" in prompt
    assert "2026-08-15T00:00:00+09:00" in prompt


def test_등록된_카메라_식별자를_그대로_알려준다() -> None:
    prompt = _prompt(
        [
            CameraChoice(
                camera_id="camera-01",
                classroom_id="room-a101",
                label="A101 앞문",
                classroom_code="A101",
                classroom_name="1강의실",
            ),
            CameraChoice(
                camera_id="camera-02",
                classroom_id="room-b203",
                label="B203 뒷문",
                classroom_code="B203",
                classroom_name="2강의실",
            ),
        ]
    )

    assert "camera_id=camera-01" in prompt
    assert "classroom_id=room-b203" in prompt
    assert "지어내지 마라" in prompt


def test_카메라가_없으면_null만_쓰라고_알린다() -> None:
    """빈 목록을 그대로 보여주면 모델이 빈칸을 채우려 든다."""
    prompt = _prompt([])

    assert "등록된 카메라가 없다" in prompt


def test_계약의_핵심_규칙이_프롬프트에_들어_있다() -> None:
    prompt = _prompt([])

    assert "detection_search" in prompt
    assert f"{MAX_LIMIT} 이하" in prompt
    assert "다른 키를 넣지 마라" in prompt


def test_목록에_없는_곳도_들은_이름을_적으라고_지시한다() -> None:
    """null로 뭉개면 **없는 강의실을 물은 사람과 아무 곳도 말하지 않은 사람이 같아진다.**

    서버는 null을 "전체 카메라"로 해석하므로, 없는 곳을 물은 사용자는 안내 대신
    엉뚱한 전체 결과를 받는다. 등록 여부 판정과 안내 문구는 service.py의
    `_resolve_targets`가 갖고 있고, 모델이 미리 판단해 버리면 그 경로에 닿지 못한다.
    """
    prompt = _prompt(
        [
            CameraChoice(
                camera_id="camera-01",
                classroom_id="room-a101",
                label="A101 앞문",
                classroom_code="A101",
                classroom_name="1강의실",
            )
        ]
    )

    assert "들은 이름을 그대로 classroom_id에 적는다" in prompt
    assert "안내는 서버가 한다" in prompt
    # 등록된 곳이 하나뿐일 때 모델이 그쪽으로 끌리는 것을 막으려는 문장이다.
    assert "목록에 하나만 있어도 그것으로 바꾸지 마라" in prompt


def test_아무_곳도_말하지_않았을_때만_null이라고_지시한다() -> None:
    """ "없으면 null"과 "말하지 않았으면 null"은 다르다. 둘을 섞으면 위 구분이 무너진다."""
    prompt = _prompt(
        [
            CameraChoice(
                camera_id="camera-01",
                classroom_id="room-a101",
                label="A101 앞문",
                classroom_code="A101",
                classroom_name="1강의실",
            )
        ]
    )

    assert "아무 곳도 말하지 않았을 때만 null이다" in prompt


def test_분과_초를_그대로_옮기라고_지시한다() -> None:
    """2026-08-25 실측: "어제 16시 30분부터 17시 사이"가 16:00~17:00으로 해석됐다.

    이전 지시문의 예시가 전부 정시라 모델이 "시"만 읽고 "분"을 버렸다. **검증이
    잡지 못하는 실패다** — 16:00도 형식상 완벽한 시각이라 그대로 통과하고, 사용자는
    자기가 묻지 않은 30분을 포함한 결과를 맞는 답으로 읽는다.
    """
    prompt = _prompt([])

    assert "정시로 반올림하지 마라" in prompt
    # 예시를 정시가 아닌 값으로 준다. 정시 예시만 있으면 같은 실패가 되돌아온다.
    assert "16:30:00" in prompt
    assert "16:30:20" in prompt


def test_어제_날짜를_직접_세지_않게_한다() -> None:
    """`tomorrow_kst`와 같은 이유다. 월초의 "어제"에서 모델이 날짜를 틀린다."""
    prompt = _prompt([])

    # _NOW는 KST로 2026-08-14 09:30이다.
    assert "2026-08-13" in prompt
    assert "직접 세지 마라" in prompt


def test_강의실만_말했으면_카메라를_고르지_말라고_지시한다() -> None:
    """2026-08-25 GPU 서버(gemma) 실측: "A111에 누가 있었어?"에 모델이 강의실과 함께
    `camera_id="classroom-cctv"`를 냈다.

    `_resolve_targets`는 camera_id를 우선하므로 **같은 강의실의 다른 카메라에 찍힌
    기록이 통째로 빠진다.** 검증이 잡지 못한다 — 실재하는 카메라라 그대로 통과한다.
    """
    prompt = _prompt([])

    assert "카메라를 콕 집어 말했을 때만 채운다" in prompt
    assert "강의실만 말했으면 camera_id는 null이다" in prompt


def test_카메라_이름을_식별자로_쓰지_말라고_지시한다() -> None:
    """같은 실측에서 `camera_id="A111 어안 카메라"`가 나왔다. 등록되지 않은 카메라로
    판정돼 0건이 된다."""
    prompt = _prompt([])

    assert "카메라를 부르는 이름을 적지 마라" in prompt


def test_오늘_하루의_시작을_지금_시각으로_잡지_말라고_지시한다() -> None:
    """같은 실측: "오늘 A111에 박무현 있는 사진"의 from이 지금 시각(14:00)으로 나와
    구간 전체가 미래가 됐고 422로 떨어졌다."""
    prompt = _prompt([])

    assert "지금 시각을 from에 적지 마라" in prompt
    assert "오늘 0시부터이지 지금부터가 아니다" in prompt
