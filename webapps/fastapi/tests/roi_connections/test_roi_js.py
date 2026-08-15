"""ROI 브라우저 코드의 카메라 범위 계약."""

from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "static" / "roi-connections.js"


def test_roi_save_payload_contains_selected_camera() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'document.querySelector("#roi-camera-select")' in source
    assert "camera_id: selectedCameraId()" in source
    assert "body.camera_id" in source


def test_roi_media_uses_explicit_camera_selection() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "await connectWebRTC(cameraId)" in source
    assert "/api/v1/video-streams?classroom_id=" not in source
    assert 'cameraSelect?.addEventListener("change"' in source
