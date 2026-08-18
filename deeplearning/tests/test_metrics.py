"""지표 정의와 기록 함수의 계약. **모델 없이 돈다.**

`app.py`는 module import 시점에 mediapipe·insightface를 요구하지만 `metrics.py`는
prometheus_client만 쓴다. 계측 자체를 그 무거운 의존 없이 확인하기 위한 분리다.

지표는 전역 레지스트리에 누적되므로 **절대값을 단정하지 않고 증분만 본다.**
"""

from __future__ import annotations

import sys
import time
from collections.abc import Iterator

import metrics
import pytest
from prometheus_client import REGISTRY


@pytest.fixture
def restore_session_gauge() -> Iterator[None]:
    """세션 Gauge는 프로세스에 하나뿐이라 바꿔 놓으면 뒤 테스트가 엉뚱한 값을 본다.

    `app.py`가 import 시점에 진짜 세션 딕셔너리를 연결하므로, 이미 로드되어 있으면
    그것으로 되돌린다. 없으면 0을 돌려주는 것으로 둔다.
    """
    yield
    module = sys.modules.get("deeplearning.app")
    if module is None:
        metrics.install_session_gauge(lambda: 0)
    else:
        metrics.install_session_gauge(module._active_session_count)


def value(name: str, **labels: str) -> float:
    sampled = REGISTRY.get_sample_value(f"{metrics.METRIC_PREFIX}{name}", labels or None)
    return 0.0 if sampled is None else float(sampled)


def test_분석_결과를_사유별로_센다() -> None:
    """no_face는 실패가 아니라 정상적인 결과라 bad_image와 섞으면 안 된다."""
    before_ok = value("face_analysis_requests_total", result="ok")
    before_no_face = value("face_analysis_requests_total", result="no_face")

    metrics.record_analysis_request("ok")
    metrics.record_analysis_request("no_face")
    metrics.record_analysis_request("no_face")

    assert value("face_analysis_requests_total", result="ok") == before_ok + 1
    assert value("face_analysis_requests_total", result="no_face") == before_no_face + 2


def test_구간별로_따로_잰다() -> None:
    """느린 쪽이 SCRFD인지 MediaPipe인지 나눠 재지 않으면 알 수 없다."""
    before_detect = value("face_analysis_duration_seconds_count", stage="detect")
    before_pose = value("face_analysis_duration_seconds_count", stage="pose")

    metrics.observe_analysis_stage("detect", time.perf_counter())

    assert value("face_analysis_duration_seconds_count", stage="detect") == before_detect + 1
    assert value("face_analysis_duration_seconds_count", stage="pose") == before_pose


def test_경과_시간을_양수로_남긴다() -> None:
    before = value("face_analysis_duration_seconds_sum", stage="quality")
    started_at = time.perf_counter()
    time.sleep(0.01)

    metrics.observe_analysis_stage("quality", started_at)

    assert value("face_analysis_duration_seconds_sum", stage="quality") >= before + 0.01


def test_embedding은_시간_없이도_셀_수_있다() -> None:
    """요청 본문을 읽기 전에 실패하면 잴 시간이 없다."""
    before_count = value("face_embedding_requests_total", result="bad_image")
    before_duration = value("face_embedding_duration_seconds_count")

    metrics.record_embedding_request("bad_image")

    assert value("face_embedding_requests_total", result="bad_image") == before_count + 1
    assert value("face_embedding_duration_seconds_count") == before_duration


def test_embedding_시간을_함께_남길_수_있다() -> None:
    before = value("face_embedding_duration_seconds_count")

    metrics.record_embedding_request("ok", time.perf_counter())

    assert value("face_embedding_duration_seconds_count") == before + 1


def test_세션_수를_스크랩할_때_읽는다(restore_session_gauge: None) -> None:
    """세션이 생기고 사라질 때마다 Gauge를 건드리면 잠금 경로에 일이 늘어난다."""
    sessions: dict[str, object] = {}
    metrics.install_session_gauge(lambda: len(sessions))

    assert value("face_analysis_sessions_active") == 0

    sessions["enrollment-1"] = object()
    sessions["enrollment-2"] = object()

    assert value("face_analysis_sessions_active") == 2

    sessions.clear()

    assert value("face_analysis_sessions_active") == 0


def test_지표를_텍스트로_내보낸다() -> None:
    body, content_type = metrics.render_metrics()

    assert b"classroom_monitoring_face_analysis_requests_total" in body
    assert "text/plain" in content_type
