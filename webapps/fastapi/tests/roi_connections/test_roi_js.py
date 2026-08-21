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


def test_saved_connections_are_loaded_and_drawn() -> None:
    """이미 그린 ROI가 화면에 보이지 않으면 좌석이 스무 개일 때 등록을 끝낼 수 없다."""
    source = SCRIPT.read_text(encoding="utf-8")

    assert "roi-connections?camera_id=" in source
    assert "const loadConnections" in source
    assert "savedShapes.replaceChildren" in source
    # 좌석 이름이 없으면 어느 폴리곤이 어느 자리인지 알 수 없다.
    assert "roi-saved-label" in source


def test_recapture_warns_before_invalidating_saved_rois() -> None:
    """재캡처는 기존 ROI를 전부 재검토 대상으로 만든다. 조용히 일어나면 안 된다."""
    source = SCRIPT.read_text(encoding="utf-8")

    assert "window.confirm" in source
    assert "재검토 대상이 되어" in source


def test_saved_roi_can_be_redrawn_or_deleted() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'method: "DELETE"' in source
    assert "const redrawSelected" in source
    # 다시 그리기는 대상 좌석이 정해져 있다. 좌석을 바꾸면 다른 자리를 덮어쓴다.
    assert "seatSelect.disabled = redrawSeatId !== null" in source


def test_review_state_is_not_signalled_by_colour_alone() -> None:
    """상태를 색으로만 구분하지 않는다(AGENTS.md 화면 규칙)."""
    source = SCRIPT.read_text(encoding="utf-8")
    style = (SCRIPT.parent / "roi-connections.css").read_text(encoding="utf-8")

    assert "재검토" in source
    assert "stroke-dasharray" in style
