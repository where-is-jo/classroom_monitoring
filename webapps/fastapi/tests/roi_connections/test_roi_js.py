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


def test_auto_generated_rois_need_an_explicit_confirmation() -> None:
    """확정 없이 판정에 들어가면 계산 오차가 그대로 출결 기록이 된다(결정 0020의 6번)."""
    source = SCRIPT.read_text(encoding="utf-8")

    assert "roi-connections/auto/confirm" in source
    assert "const confirmAutoRoi" in source
    assert "auto_generated" in source
    assert "window.confirm" in source


def test_preview_is_not_signalled_by_colour_alone() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    style = (SCRIPT.parent / "roi-connections.css").read_text(encoding="utf-8")

    assert "미리보기" in source
    assert "#roi-auto-preview polygon" in style
    assert "stroke-dasharray: 3 4" in style


def test_detection_spots_are_assigned_by_a_person_not_guessed() -> None:
    """카메라는 자리를 알지만 좌석 이름을 알지 못한다(결정 0041)."""
    source = SCRIPT.read_text(encoding="utf-8")

    assert "from-detections" in source
    assert "const findSpots" in source
    assert "const saveSpots" in source
    # 자리마다 좌석을 고르는 줄을 만든다.
    assert "renderDetectPanel" in source
    assert "저장하지 않음" in source


def test_detection_result_distinguishes_no_data_from_no_spots() -> None:
    """ "탐지가 없다"와 "자리로 인정할 곳이 없다"는 다른 사실이다."""
    source = SCRIPT.read_text(encoding="utf-8")

    assert "탐지 기록이 없습니다" in source
    assert "오래 머문 곳이 없었습니다" in source


def test_detection_preview_is_not_signalled_by_colour_alone() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    style = (SCRIPT.parent / "roi-connections.css").read_text(encoding="utf-8")

    assert '"탐지"' in source
    assert "polygon[data-spot-index]" in style
    assert "stroke-dasharray: 6 3" in style


def test_finding_spots_captures_the_screen_by_itself() -> None:
    """찾기를 누르면 바탕까지 갖춰져야 한다. 캡처를 따로 누르게 하면 빈 화면에 좌표만 뜬다."""
    source = SCRIPT.read_text(encoding="utf-8")

    assert "captureFrame({quiet: true})" in source
    assert "referenceRevision === null && captureAvailable()" in source


def test_found_spots_get_a_seat_so_only_saving_is_left() -> None:
    """자리마다 좌석을 손으로 고르게 하면 스무 번 클릭이 남는다."""
    source = SCRIPT.read_text(encoding="utf-8")

    assert "const assignSeats" in source
    # 추측이므로 바꿀 수 있어야 한다.
    assert "저장하지 않음" in source


def test_recapture_warning_ignores_rois_that_do_not_depend_on_the_screen() -> None:
    """탐지 기반 ROI는 재캡처로 무효가 되지 않는다. 무관한 것까지 경고하면 경고가 무뎌진다."""
    source = SCRIPT.read_text(encoding="utf-8")

    assert "item.reference_image_revision > 0" in source


def test_camera_is_fixed_when_only_one_seat_judging_camera_exists() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "#roi-camera-fixed" in source
    assert "fixedCamera?.dataset.cameraId" in source


def test_capture_stage_is_scaled_to_fit_the_page() -> None:
    """1280x1944 원본을 그대로 두면 페이지가 한없이 길어진다.

    stage와 이미지의 상자가 같아야 ROI 좌표가 맞으므로 stage 크기를 이미지가 정한다.
    다만 너무 줄이면 좌석 하나가 몇 십 px이라 ROI를 눈으로 대조할 수 없다. 화면이
    낮은 기기에서도 최소 크기를 확보하려고 vh와 절대값 중 큰 쪽을 쓴다.
    """
    style = (SCRIPT.parent / "roi-connections.css").read_text(encoding="utf-8")

    assert "max-height: max(92vh, 820px)" in style
    assert "width: fit-content" in style


def test_toolbar_flows_horizontally_instead_of_stacking() -> None:
    style = (SCRIPT.parent / "roi-connections.css").read_text(encoding="utf-8")

    assert ".roi-filter-row { display: flex; justify-content: space-between; align-items: center; gap: 5px;" in style
    assert ".roi-filter-wrap { display: flex; justify-content: center; align-items: center; gap: 8px;" in style
    assert "flex-direction: column-reverse" not in style


def test_seats_without_roi_are_listed_instead_of_silently_missing() -> None:
    """탐지는 사람이 앉았던 자리만 찾는다.

    아무도 앉지 않은 좌석은 표본이 없어 영영 나오지 않으므로, 남은 좌석을 화면이
    드러내지 않으면 관리자는 어느 좌석이 비었는지 20개를 눈으로 대조해야 한다.
    """
    source = SCRIPT.read_text(encoding="utf-8")

    assert "const renderMissingSeats" in source
    # 좌석 목록과 등록된 ROI가 이미 화면에 있으므로 서버에 따로 묻지 않는다.
    assert "savedConnections.map((item) => item.seat_id)" in source
    assert "seatOptionValues().filter" in source


def test_manual_draw_targets_one_seat_and_skips_the_dialog() -> None:
    """직접 그리기는 좌석이 이미 정해져 있다.

    좌석을 다시 고르게 하면 다른 좌석을 덮어쓸 수 있고, 학생 배정의 정본은
    seat_assignments이므로(결정 0019의 6번) 좌석 ROI만 만든다.
    """
    source = SCRIPT.read_text(encoding="utf-8")

    assert "const startManualDraw" in source
    assert "const saveManualRoi" in source
    assert "if (manualSeatId !== null) {" in source
    assert "student_id" not in source.split("const saveManualRoi")[1].split("};")[0]


def test_manual_draw_needs_a_captured_screen_first() -> None:
    """좌표는 캡처 화면 위의 상대 위치다. 바탕이 없으면 어디를 찍는지 알 수 없다."""
    source = SCRIPT.read_text(encoding="utf-8")

    body = source.split("const startManualDraw")[1]
    assert "referenceRevision === null" in body.split("};")[0]
