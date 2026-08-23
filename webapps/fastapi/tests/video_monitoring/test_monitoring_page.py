"""/monitoring 페이지 server page 테스트 (MON-001, MON-003, MON-004, MON-006).

real-only 렌더링, demo/filter markup 부재, 단일/복수 카메라 grid, 빈 상태,
반응형 CSS를 SSR 결과와 정적 CSS로 검증한다. 실카메라·학생 영상은 사용하지 않는다.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.shared.dependencies import get_video_stream_service
from app.video_monitoring.adapters.memory_repository import MemoryVideoStreamRepository
from app.video_monitoring.models import VideoStream
from app.video_monitoring.service import VideoStreamService

from .fakes import NOW, FakeClock, make_classroom_service, make_stream

STATIC_CSS = Path(__file__).resolve().parents[2] / "static" / "style.css"
STALE_SECONDS = 300


def _make_service(*streams: VideoStream) -> VideoStreamService:
    repository = MemoryVideoStreamRepository()
    for stream in streams:
        repository.save(stream)
    return VideoStreamService(
        repository,
        make_classroom_service(),
        stale_seconds=STALE_SECONDS,
        clock=FakeClock(),
    )


@pytest.fixture
def monitoring_client() -> Iterator[TestClient]:
    """real 2대 + demo 1대 + disabled 1대가 등록된 화면 client."""
    service = _make_service(
        make_stream(stream_id="stream-01", camera_id="camera-01"),
        make_stream(stream_id="stream-02", camera_id="camera-02"),
        make_stream(stream_id="stream-demo", camera_id="camera-demo", is_demo=True),
        make_stream(stream_id="stream-off", camera_id="camera-off", enabled=False),
    )
    app.dependency_overrides[get_video_stream_service] = lambda: service
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@contextmanager
def _client_with(service: VideoStreamService) -> Iterator[TestClient]:
    app.dependency_overrides[get_video_stream_service] = lambda: service
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


# ── real-only 렌더링 (MON-001, MON-002) ─────────────────────────────────────


def test_monitoring_page_renders_real_streams_only(monitoring_client: TestClient) -> None:
    response = monitoring_client.get("/monitoring")

    assert response.status_code == 200
    assert 'data-camera-id="camera-01"' in response.text
    assert 'data-camera-id="camera-02"' in response.text
    # 카메라 식별자·강의실 식별자·label이 영상 아래에 노출된다.
    assert "camera-01" in response.text
    assert "classroom-a101" in response.text
    assert "A101 전면 카메라" in response.text
    # demo/disabled source는 렌더링되지 않는다 (MON-002).
    assert "camera-demo" not in response.text
    assert "camera-off" not in response.text
    # WebRTC 재생 video가 카드마다 있다.
    assert response.text.count("data-webrtc") == 2


def test_monitoring_page_has_no_filter_or_demo_markup(monitoring_client: TestClient) -> None:
    """MON-001: source filter form·query UI·합성 demo source/script/ribbon/is_demo 부재."""
    response = monitoring_client.get("/monitoring")

    assert response.status_code == 200
    for removed in (
        "<form",
        'name="q"',
        'name="classroom_id"',
        'name="status"',
        "filter-form",
        "source 필터",
        "demo-video-grid",
        "demo-video-card",
        "demo-ribbon",
        "demo-clock",
        "합성",
        "synthetic",
        "is_demo",
        "demo-assets",
        "실제 CCTV나 실시간 스트림",
    ):
        assert removed not in response.text, f"제거 대상 markup이 남아 있음: {removed}"


# ── 카드 구성 (MON-004) ─────────────────────────────────────────────────────


def test_monitoring_page_card_has_identity_status_detection_and_webrtc(
    monitoring_client: TestClient,
) -> None:
    """각 카드는 카메라·강의실 식별자, 탐지 수신 text 상태, 마지막 탐지,
    WebRTC 재생과 카드 한정 오류 alert를 제공한다."""
    response = monitoring_client.get("/monitoring")

    assert response.status_code == 200
    assert "data-real-stream" in response.text
    assert "data-stream-id" in response.text
    assert "data-video-error" in response.text
    assert 'role="alert"' in response.text
    assert "data-last-detection" in response.text
    assert "data-detection-count" in response.text
    assert "마지막 탐지" in response.text
    # video 접근 가능한 이름에는 카메라 label과 실시간 영상임이 포함된다.
    assert 'aria-label="A101 전면 카메라 실시간 영상"' in response.text
    assert "muted autoplay playsinline" in response.text
    # last_detection_at이 없는 fixture stream은 "상태 확인 중"으로 표시된다.
    assert "상태 확인 중" in response.text


def test_monitoring_page_renders_detection_status_text_by_freshness() -> None:
    """탐지 수신 상태는 최근 탐지 시각 기준 text·색상으로 병기된다 (MON-004)."""
    service = _make_service(
        replace(
            make_stream(stream_id="stream-fresh", camera_id="camera-fresh"),
            last_detection_at=NOW - timedelta(seconds=60),
        ),
        replace(
            make_stream(stream_id="stream-stale", camera_id="camera-stale"),
            last_detection_at=NOW - timedelta(seconds=STALE_SECONDS + 150),
        ),
        replace(
            make_stream(stream_id="stream-gone", camera_id="camera-gone"),
            last_detection_at=NOW - timedelta(seconds=STALE_SECONDS * 3),
        ),
        make_stream(stream_id="stream-unknown", camera_id="camera-unknown"),
    )
    with _client_with(service) as client:
        response = client.get("/monitoring")

    assert response.status_code == 200
    assert "탐지 수신 중" in response.text
    assert "탐지 지연" in response.text
    assert "최근 탐지 없음" in response.text
    assert "상태 확인 중" in response.text


# ── grid 배치 (MON-003) ─────────────────────────────────────────────────────


def test_monitoring_page_two_or_more_cameras_use_two_column_grid(
    monitoring_client: TestClient,
) -> None:
    response = monitoring_client.get("/monitoring")

    assert response.status_code == 200
    assert 'class="camera-monitoring-grid"' in response.text
    assert "camera-monitoring-grid--single" not in response.text


def test_monitoring_page_single_camera_uses_full_width_grid() -> None:
    """카메라 한 대는 전체 폭을 사용한다 (MON-003)."""
    service = _make_service(make_stream(stream_id="stream-01", camera_id="camera-01"))
    with _client_with(service) as client:
        response = client.get("/monitoring")

    assert response.status_code == 200
    assert 'class="camera-monitoring-grid camera-monitoring-grid--single"' in response.text


def test_monitoring_grid_css_supports_large_two_column_responsive_layout() -> None:
    """최대 2열(desktop)·1열(900px 이하) 대형 grid와 카메라 비율을 따르는 프레임 CSS."""
    css = STATIC_CSS.read_text(encoding="utf-8")

    assert ".camera-monitoring-grid {" in css
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in css
    assert ".camera-monitoring-grid--single { grid-template-columns: minmax(0, 1fr); }" in css
    # 900px 이하에서 한 열로 전환한다.
    media_start = css.index("@media (max-width: 900px)")
    next_media = css.find("@media", media_start + 1)
    segment = css[media_start : next_media if next_media != -1 else len(css)]
    assert ".camera-monitoring-grid" in segment
    assert "grid-template-columns: minmax(0, 1fr)" in segment
    # 카드 영상 영역의 비율은 카메라를 따라간다. 16:9로 박아 두면 세로가 긴 어안
    # CCTV(1280x1944)가 위아래로 잘려 앞뒷줄 좌석이 화면에서 사라진다.
    assert "aspect-ratio: var(--frame-aspect, 16 / 9)" in css
    # 잘라내는 cover가 아니라 전체를 담는 contain이어야 한다.
    assert (
        ".camera-monitoring-frame video { width: 100%; height: 100%; object-fit: contain; }" in css
    )


# ── 전체화면 (MON-008, TASK-006) ───────────────────────────────────────────


def test_monitoring_page_has_fullscreen_toggle(monitoring_client: TestClient) -> None:
    """header에 전체화면 토글 버튼과 상태 라벨·aria-pressed가 있다."""
    response = monitoring_client.get("/monitoring")

    assert response.status_code == 200
    assert "data-fullscreen-toggle" in response.text
    assert 'aria-pressed="false"' in response.text
    assert "data-fullscreen-label" in response.text
    assert "전체화면" in response.text
    # 상태는 색상만이 아니라 text 라벨로도 제공된다.
    assert "data-fullscreen-toggle" in response.text


def test_monitoring_fullscreen_css_uses_fullscreen_selector() -> None:
    """전체화면은 main:fullscreen으로만 확대하고 sidebar·footer는 제외한다."""
    css = STATIC_CSS.read_text(encoding="utf-8")

    assert "main:fullscreen" in css
    assert ".fullscreen-toggle" in css


# ── 빈 상태 (MON-006) ───────────────────────────────────────────────────────


def test_monitoring_page_empty_state_when_no_real_cameras() -> None:
    """실제 카메라가 없으면 하나의 안전한 빈 상태만 렌더링한다."""
    with _client_with(_make_service()) as client:
        response = client.get("/monitoring")

    assert response.status_code == 200
    assert "연결된 카메라가 없습니다." in response.text
    assert "학생 부재로 해석하지 않습니다" in response.text
    assert 'role="status"' in response.text
    assert "data-real-stream" not in response.text
    assert "data-webrtc" not in response.text
    assert "camera-monitoring-grid" not in response.text


def test_monitoring_page_empty_state_when_only_demo_or_disabled_streams() -> None:
    """demo·disabled만 있으면 카드가 아니라 빈 상태를 렌더링한다 (MON-002, MON-006)."""
    service = _make_service(
        make_stream(stream_id="stream-demo", camera_id="camera-demo", is_demo=True),
        make_stream(stream_id="stream-off", camera_id="camera-off", enabled=False),
    )
    with _client_with(service) as client:
        response = client.get("/monitoring")

    assert response.status_code == 200
    assert "연결된 카메라가 없습니다." in response.text
    assert "camera-demo" not in response.text
    assert "camera-off" not in response.text
