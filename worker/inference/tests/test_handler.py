"""탐지 결과를 FastAPI로 전송하는 핸들러 검증.

실제 HTTP 요청을 보내지 않는다. `post`와 `sleep`을 대역으로 바꿔 넣는다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import numpy as np
import requests
from shared.types import CapturedFrame

from ..handler import FastAPIResultHandler, build_event_id
from ..types import Detection, InferenceResult


def build_captured(
    sequence: int = 7, *, camera_id: str = "camera-01"
) -> CapturedFrame:
    return CapturedFrame(
        camera_id=camera_id,
        frame=np.zeros((2, 2, 3), dtype=np.uint8),
        captured_at=datetime(2026, 8, 10, 9, 3, 0, tzinfo=UTC),
        sequence=sequence,
    )


def build_result(count: int = 1) -> InferenceResult:
    detections = tuple(
        Detection(class_id=0, class_name="person", confidence=0.9, bbox=(10, 20, 30, 40))
        for _ in range(count)
    )
    return InferenceResult(frame_shape=(480, 640, 3), detections=detections)


class FakeResponse:
    """requests.Response 대역. 상태 코드만으로 raise_for_status를 흉내 낸다."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)


class FakePoster:
    """정해진 순서대로 응답이나 예외를 돌려주고 호출을 기록한다."""

    def __init__(self, outcomes: list[object]) -> None:
        self._outcomes = outcomes
        self.calls: list[tuple[str, dict[str, Any], float]] = []

    def __call__(
        self, url: str, *, json: dict[str, Any], timeout: float
    ) -> requests.Response:
        self.calls.append((url, json, timeout))
        index = min(len(self.calls) - 1, len(self._outcomes) - 1)
        outcome = self._outcomes[index]
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(int(outcome))  # type: ignore[arg-type]


class FakeSleeper:
    """재시도 backoff 대기 시간을 기록한다."""

    def __init__(self) -> None:
        self.durations: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.durations.append(seconds)


def build_handler(
    outcomes: list[object], *, fastapi_url: str = "http://fastapi:8000"
) -> tuple[FastAPIResultHandler, FakePoster, FakeSleeper]:
    poster = FakePoster(outcomes)
    sleeper = FakeSleeper()
    handler = FastAPIResultHandler(
        fastapi_url,
        post=poster,  # type: ignore[arg-type]
        sleep=sleeper,
        inner=lambda captured, result: None,
    )
    return handler, poster, sleeper


def test_탐지_결과를_fastapi로_전송한다() -> None:
    """정상 전송(201). 경로·타임아웃·본문 형태와 필드 값을 확인한다."""
    handler, poster, sleeper = build_handler([201])
    captured = build_captured()
    result = build_result(2)

    handler(captured, result)

    assert len(poster.calls) == 1
    url, payload, timeout = poster.calls[0]
    assert url == "http://fastapi:8000/internal/inference/events"
    assert timeout == 5.0
    assert payload["camera_id"] == "camera-01"
    assert payload["sequence"] == 7
    assert payload["captured_at"] == "2026-08-10T09:03:00+00:00"
    # frame_shape=(480, 640, 3) → 가로 640, 세로 480
    assert payload["frame"] == {"width_pixels": 640, "height_pixels": 480}
    assert len(payload["detections"]) == 2
    assert sleeper.durations == [], "성공하면 재시도 대기가 없어야 한다"


def test_event_id는_카메라_시각_프레임번호로_만든다() -> None:
    captured = build_captured(sequence=42, camera_id="class-a-left")

    assert build_event_id(captured) == "class-a-left-20260810T090300000Z-42"


def test_event_id는_밀리초_3자리를_담는다() -> None:
    captured = CapturedFrame(
        camera_id="cam",
        frame=np.zeros((2, 2, 3), dtype=np.uint8),
        captured_at=datetime(2026, 8, 10, 9, 3, 0, 123000, tzinfo=UTC),
        sequence=1,
    )

    assert build_event_id(captured) == "cam-20260810T090300123Z-1"


def test_detection_id는_event_id에_인덱스를_붙인다() -> None:
    handler, poster, _ = build_handler([201])
    captured = build_captured(sequence=3, camera_id="cam-2")

    handler(captured, build_result(2))

    event_id = poster.calls[0][1]["event_id"]
    assert [d["detection_id"] for d in poster.calls[0][1]["detections"]] == [
        f"{event_id}-det-0",
        f"{event_id}-det-1",
    ]


def test_탐지가_없어도_빈_목록으로_전송한다() -> None:
    handler, poster, sleeper = build_handler([201])

    handler(build_captured(), build_result(0))

    assert len(poster.calls) == 1
    assert poster.calls[0][1]["detections"] == []
    assert sleeper.durations == []


def test_전송_실패가_예외를_전파하지_않는다() -> None:
    handler, poster, sleeper = build_handler([requests.ConnectionError("연결 실패")])

    handler(build_captured(), build_result())

    # 초기 1회 + 재시도 2회, 사이 대기는 1초·2초 지수 backoff
    assert len(poster.calls) == 3
    assert sleeper.durations == [1.0, 2.0]


def test_연결_오류가_아닌_예외도_전파하지_않는다() -> None:
    """본문 직렬화 같은 뜻밖의 예외가 소비자 루프를 죽이면 안 된다."""
    handler, poster, sleeper = build_handler([ValueError("직렬화 오류")])

    handler(build_captured(), build_result())

    assert len(poster.calls) == 3
    assert sleeper.durations == [1.0, 2.0]


def test_HTTP_오류_상태_코드는_재시도_끝에_로그만_남긴다() -> None:
    handler, poster, sleeper = build_handler([500])

    handler(build_captured(), build_result())

    assert len(poster.calls) == 3
    assert sleeper.durations == [1.0, 2.0]


def test_재시도_끝에_성공하면_한_번만_성공한다() -> None:
    handler, poster, sleeper = build_handler([requests.Timeout("시간 초과"), 201])

    handler(build_captured(), build_result())

    assert len(poster.calls) == 2
    assert sleeper.durations == [1.0]


def test_backoff_목록이_재시도_횟수보다_짧으면_마지막_값을_쓴다() -> None:
    """인덱스 경계 예외가 파이프라인을 죽이면 안 된다."""
    poster = FakePoster([requests.ConnectionError("연결 실패")])
    sleeper = FakeSleeper()
    handler = FastAPIResultHandler(
        "http://fastapi:8000",
        post=poster,  # type: ignore[arg-type]
        sleep=sleeper,
        max_retries=3,
        backoff_seconds=(1.0,),
        inner=lambda captured, result: None,
    )

    handler(build_captured(), build_result())

    assert len(poster.calls) == 4  # 초기 1회 + 재시도 3회
    assert sleeper.durations == [1.0, 1.0, 1.0]


def test_기본_url_끝의_슬래시를_정리한다() -> None:
    handler, poster, _ = build_handler([201], fastapi_url="http://fastapi:8000/")

    handler(build_captured(), build_result())

    assert poster.calls[0][0] == "http://fastapi:8000/internal/inference/events"


def test_기존_로그_핸들러를_먼저_호출한다() -> None:
    """HTTP 전송만 켰다고 탐지 로그가 사라지면 안 된다."""
    calls: list[int] = []
    poster = FakePoster([201])
    sleeper = FakeSleeper()
    handler = FastAPIResultHandler(
        "http://fastapi:8000",
        post=poster,  # type: ignore[arg-type]
        sleep=sleeper,
        inner=lambda captured, result: calls.append(len(result.detections)),
    )

    handler(build_captured(), build_result(2))

    assert calls == [2]
