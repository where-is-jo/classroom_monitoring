"""ROI 브라우저 코드의 카메라 범위와 기준 화면 계약."""

from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "static" / "roi-connections.js"


def test_roi_save_payload_contains_selected_camera() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'document.querySelector("#roi-camera-select")' in source
    assert "camera_id: selectedCameraId()" in source
    assert "body.camera_id" in source


def test_roi_media_uses_explicit_camera_selection() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "/api/v1/video-streams?classroom_id=" not in source
    assert 'cameraSelect?.addEventListener("change"' in source


def test_roi_background_comes_from_capture_not_direct_media_server() -> None:
    """브라우저가 미디어 서버에 직접 붙지 않는다(AGENTS.md 아키텍처 규칙 1번).

    이전 구현은 `http://<host>:8889/<camera>/whep`로 MediaMTX에 직접 붙었는데,
    그 포트는 어느 환경에서도 호스트에 열려 있지 않아 항상 실패했다.
    """
    source = SCRIPT.read_text(encoding="utf-8")

    assert "8889" not in source
    assert "RTCPeerConnection" not in source
    assert "roi-reference-image/capture" in source


def test_roi_save_sends_reference_revision() -> None:
    """기준 화면이 바뀌면 저장이 거절되도록 revision을 함께 보낸다."""
    source = SCRIPT.read_text(encoding="utf-8")

    assert "reference_image_revision: referenceRevision" in source
    assert "/roi-connection" in source
