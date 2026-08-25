"""monitoring.js 정적 검증 + 브라우저 player lifecycle 테스트 (MON-005, SPEC §6).

정적 검증(브라우저 불필요):
  - bundle에 :8889/MediaMTX host/RTSP credential 부재 (MON-005)
  - TASK-003 data attribute 사용
  - playback session/signaling/SSE/unload 계약 존재

브라우저 검증(Edge/Chrome headless, 없으면 skip):
  - playback session 생성·삭제
  - WebRTC signaling 연결
  - SSE 구독과 bbox·탐지 수·마지막 탐지 갱신
  - 오류 카드 한정
  - unload 시 EventSource·RTCPeerConnection·session 정리

실카메라·학생 영상은 사용하지 않고, 네트워크·WebRTC·SSE를 대역으로 교체해
실제 브라우저 JS 엔진에서 monitoring.js를 실행한다.
"""

from __future__ import annotations

import html as html_module
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import cast

import pytest

BASE = Path(__file__).resolve().parents[2]
MONITORING_JS = BASE / "static" / "monitoring.js"
HARNESS_TEMPLATE = Path(__file__).parent / "browser_harness" / "monitoring_test.html"

_BROWSER_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)

_MEDIA_MTX_LEAKS = (":8889", "mediamtx", "rtsp://", "rtsp:")


def _find_browser() -> str | None:
    for candidate in _BROWSER_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


BROWSER = _find_browser()


@pytest.fixture(scope="module")
def browser_results() -> dict[str, object]:
    """headless 브라우저에서 monitoring.js를 실행한 결과 JSON."""
    if BROWSER is None:
        pytest.skip("Edge/Chrome headless가 없어 브라우저 테스트를 건너뜁니다.")
    harness = HARNESS_TEMPLATE.read_text(encoding="utf-8")
    harness = harness.replace("{{ MONITORING_JS_URI }}", MONITORING_JS.resolve().as_uri())

    # 브라우저 첫 실행 프로필 충돌을 피하기 위해 사용자 데이터를 임시 디렉터리에 둔다.
    with tempfile.TemporaryDirectory() as profile_dir:
        html_file = Path(profile_dir) / "monitoring_test.html"
        html_file.write_text(harness, encoding="utf-8")

        command = [
            BROWSER,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--allow-file-access-from-files",
            "--virtual-time-budget=5000",
            f"--user-data-dir={profile_dir}",
            "--dump-dom",
            html_file.as_uri(),
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        dom = completed.stdout

    if not dom:
        raise AssertionError(f"브라우저 출력이 비어 있습니다. stderr: {completed.stderr[:2000]}")
    return _extract_results(dom)


def _extract_results(dom: str) -> dict[str, object]:
    match = re.search(r'<pre id="test-results">(.*?)</pre>', dom, re.DOTALL)
    if not match:
        raise AssertionError("dump-dom에서 test-results 요소를 찾지 못했습니다.")
    text = html_module.unescape(match.group(1))
    return cast(dict[str, object], json.loads(text))


def _checkpoints(results: dict[str, object]) -> list[dict[str, object]]:
    value = results.get("checkpoints", [])
    assert isinstance(value, list)
    return [item for item in value if isinstance(item, dict)]


def _assert_all_checkpoints(results: dict[str, object]) -> None:
    assert results.get("done"), f"시나리오가 완료되지 않았습니다: {results.get('error')}"
    failed = [c for c in _checkpoints(results) if not c["ok"]]
    assert not failed, f"실패한 검증: {failed}"


def _checkpoint(results: dict[str, object], name: str) -> dict[str, object]:
    for checkpoint in _checkpoints(results):
        if checkpoint["name"] == name:
            return checkpoint
    raise AssertionError(f"checkpoint가 없습니다: {name}")


# ── 정적 검증 (브라우저 불필요, MON-005) ─────────────────────────────────────


class TestMonitoringJsStatic:
    def test_bundle_has_no_media_mtx_exposure(self) -> None:
        """브라우저 bundle에 MediaMTX 주소·포트·RTSP credential이 없다 (MON-005)."""
        source = MONITORING_JS.read_text(encoding="utf-8")
        for forbidden in _MEDIA_MTX_LEAKS:
            assert forbidden not in source, f"MediaMTX 노출 문자열이 남아 있음: {forbidden}"

    def test_bundle_uses_required_data_attributes(self) -> None:
        """TASK-003 markup의 data attribute를 모두 사용한다."""
        source = MONITORING_JS.read_text(encoding="utf-8")
        # attribute 선택자로 직접 참조하는 경우
        for attribute in (
            "data-real-stream",
            "data-webrtc",
            "data-video-error",
            "data-last-detection",
            "data-detection-count",
            "data-source-status",
            "data-analysis-status",
        ):
            assert attribute in source, f"data attribute 미사용: {attribute}"
        # template의 data-stream-id/data-camera-id는 dataset으로 읽는다.
        assert "dataset.streamId" in source
        assert "dataset.cameraId" in source
        assert "dataset.cameraRole" in source

    def test_bundle_implements_playback_session_contract(self) -> None:
        """FastAPI playback session/signaling/SSE/unload cleanup 계약이 있다."""
        source = MONITORING_JS.read_text(encoding="utf-8")
        assert "/playback-sessions" in source  # session 생성·signaling URL
        assert 'method: "POST"' in source  # session 생성·signaling offer
        assert 'method: "DELETE"' in source  # unload에서 session 폐기 (idempotent)
        assert "detection-events" in source  # SSE 구독
        assert "entry-identity-events/stream" in source  # 입구 얼굴 SSE 구독
        assert "beforeunload" in source  # unload cleanup
        assert "pagehide" in source
        assert "EventSource" in source
        assert "RTCPeerConnection" in source

    def test_bbox_label_uses_server_verified_display_label(self) -> None:
        """모델 class_name을 이름처럼 표시하지 않고 서버 보강 라벨만 사용한다."""
        source = MONITORING_JS.read_text(encoding="utf-8")
        assert 'det.display_label || "사람"' in source
        assert "det.class_name +" not in source

    def test_bbox_label_includes_track_id_when_available(self) -> None:
        source = MONITORING_JS.read_text(encoding="utf-8")
        assert 'det.track_id ? " #" + det.track_id : ""' in source

    def test_bundle_implements_fullscreen_toggle(self) -> None:
        """전체화면 토글은 Fullscreen API로 main 영역만 확대/복원한다 (MON-008)."""
        source = MONITORING_JS.read_text(encoding="utf-8")
        assert "data-fullscreen-toggle" in source  # 버튼 참조
        assert "requestFullscreen" in source  # 진입
        assert "exitFullscreen" in source  # 복원
        assert "fullscreenchange" in source  # ESC·시스템 종료 반영
        assert "aria-pressed" in source  # 상태 text·ARIA 갱신
        assert "data-fullscreen-label" in source  # 라벨 text 갱신


# ── 브라우저 검증 (headless, 없으면 skip) ────────────────────────────────────


class TestMonitoringJsBrowser:
    def test_scenarios_all_pass(self, browser_results: dict[str, object]) -> None:
        """playback session·signaling·SSE·bbox 갱신·오류 한정·unload 정리."""
        _assert_all_checkpoints(browser_results)

    def test_playback_session_created(self, browser_results: dict[str, object]) -> None:
        """카드마다 FastAPI playback session을 생성한다."""
        _assert_all_checkpoints(browser_results)
        checkpoint = _checkpoint(browser_results, "playback-session-created")
        assert checkpoint["ok"]

    def test_webrtc_signaling_connected(self, browser_results: dict[str, object]) -> None:
        """생성 응답의 signaling URL로 WHEP offer(SDP)를 POST한다."""
        _assert_all_checkpoints(browser_results)
        checkpoint = _checkpoint(browser_results, "signaling-connected")
        assert checkpoint["ok"]

    def test_sse_subscribed(self, browser_results: dict[str, object]) -> None:
        """입구 얼굴과 CCTV 객체 탐지를 역할별 SSE로 구독한다."""
        _assert_all_checkpoints(browser_results)
        checkpoint = _checkpoint(browser_results, "sse-subscribed")
        assert checkpoint["ok"]

    def test_bbox_and_detection_metadata_updated(self, browser_results: dict[str, object]) -> None:
        """SSE detection으로 bbox·안전한 식별 라벨·탐지 metadata가 갱신된다."""
        _assert_all_checkpoints(browser_results)
        for name in (
            "bbox-drawn",
            "safe-identification-labels",
            "track-label-visible",
            "detection-count-updated",
            "last-detection-updated",
            "face-source-connected",
            "face-analysis-error-visible",
            "face-analysis-error-cleared",
            "cctv-object-detection-kept",
        ):
            checkpoint = _checkpoint(browser_results, name)
            assert checkpoint["ok"], f"{name}: {checkpoint['detail']}"

    def test_error_is_isolated_to_one_card(self, browser_results: dict[str, object]) -> None:
        """signaling 실패는 해당 카드에만 표시되고 다른 카드는 유지된다."""
        _assert_all_checkpoints(browser_results)
        for name in ("error-isolated", "other-card-kept"):
            checkpoint = _checkpoint(browser_results, name)
            assert checkpoint["ok"], f"{name}: {checkpoint['detail']}"

    def test_unload_cleans_connections_and_session(
        self, browser_results: dict[str, object]
    ) -> None:
        """unload 시 EventSource·RTCPeerConnection·playback session을 정리한다."""
        _assert_all_checkpoints(browser_results)
        for name in (
            "unload-event-source-closed",
            "unload-peer-connection-closed",
            "unload-session-deleted",
        ):
            checkpoint = _checkpoint(browser_results, name)
            assert checkpoint["ok"], f"{name}: {checkpoint['detail']}"
