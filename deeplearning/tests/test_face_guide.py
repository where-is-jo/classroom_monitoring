from deeplearning.app import _inside_guide


def test_face_center_must_fit_inside_visible_guide() -> None:
    assert _inside_guide((220, 115, 420, 365), width=640, height=480)
    assert _inside_guide((120, 115, 320, 365), width=640, height=480)
    assert _inside_guide((70, 115, 270, 365), width=640, height=480)
    assert not _inside_guide((30, 115, 230, 365), width=640, height=480)


def test_face_touching_frame_edge_is_rejected() -> None:
    assert not _inside_guide((5, 100, 205, 350), width=640, height=480)
