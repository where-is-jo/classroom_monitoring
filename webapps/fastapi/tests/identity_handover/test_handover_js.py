from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "static" / "identity-handover.js"
STYLE = SCRIPT.with_suffix(".css")


def test_editor_captures_cctv_and_draws_normalized_rectangle() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "identity-handover-reference-image/capture" in source
    assert 'overlay.addEventListener("pointerdown"' in source
    assert 'overlay.addEventListener("pointermove"' in source
    assert "classroom_entry_zone: draftZone" in source
    assert 'element.removeAttribute("hidden")' in source


def test_saved_zone_is_visible_with_text_not_colour_only() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    style = STYLE.read_text(encoding="utf-8")

    assert "worker_environment_value" in source
    assert "인계 영역" in source
    assert "stroke-dasharray" in style
