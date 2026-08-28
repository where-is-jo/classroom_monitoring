from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sync_face_recognizer_model_path import (
    synchronize_face_recognizer_model_path,
)


def build_server(
    tmp_path: Path,
    *,
    recognizer: str = "adaface",
    configured_path: str = "/models/face/buffalo_l/w600k_r50.onnx",
    include_path: bool = True,
) -> tuple[Path, Path]:
    docker_root = tmp_path / ".docker"
    env_dir = docker_root / "env"
    env_dir.mkdir(parents=True)
    model_relative = (
        "face/adaface/adaface_ir50_webface4m.onnx"
        if recognizer == "adaface"
        else "face/buffalo_l/w600k_r50.onnx"
    )
    model = docker_root / "models" / model_relative
    model.parent.mkdir(parents=True)
    model.write_bytes(b"onnx")
    path_line = (
        f"FACE_RECOGNITION_MODEL_PATH={configured_path}\n" if include_path else ""
    )
    env_file = env_dir / "deeplearning.dev.env"
    env_file.write_text(
        "FACE_GALLERY_DATABASE_URL=mongodb://secret-user:secret-password@example.invalid\n"
        f"FACE_RECOGNIZER={recognizer}\n"
        f"{path_line}"
        "FACE_EMBEDDING_COLLECTION=private-collection\n",
        encoding="utf-8",
    )
    return docker_root, env_file


def test_adaface_선택이면_경로_한_줄만_원자적으로_맞춘다(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    docker_root, env_file = build_server(tmp_path)
    before_mode = stat.S_IMODE(env_file.stat().st_mode)

    changed = synchronize_face_recognizer_model_path(docker_root)

    text = env_file.read_text(encoding="utf-8")
    assert changed is True
    assert (
        "FACE_RECOGNITION_MODEL_PATH="
        "/models/face/adaface/adaface_ir50_webface4m.onnx"
    ) in text
    assert "secret-user:secret-password" in text
    assert "FACE_EMBEDDING_COLLECTION=private-collection" in text
    assert "secret-user" not in capsys.readouterr().out
    if os.name != "nt":
        assert stat.S_IMODE(env_file.stat().st_mode) == before_mode


def test_arcface도_같은_계약_소스에서_경로를_고른다(tmp_path: Path) -> None:
    docker_root, env_file = build_server(
        tmp_path,
        recognizer="arcface",
        configured_path="/models/face/adaface/adaface_ir50_webface4m.onnx",
    )

    synchronize_face_recognizer_model_path(docker_root)

    assert (
        "FACE_RECOGNITION_MODEL_PATH=/models/face/buffalo_l/w600k_r50.onnx"
        in env_file.read_text(encoding="utf-8")
    )


def test_경로_키가_없으면_다른_값을_보존하고_추가한다(tmp_path: Path) -> None:
    docker_root, env_file = build_server(tmp_path, include_path=False)

    synchronize_face_recognizer_model_path(docker_root)

    text = env_file.read_text(encoding="utf-8")
    assert text.count("FACE_RECOGNITION_MODEL_PATH=") == 1
    assert "secret-user:secret-password" in text


def test_이미_정확하면_파일을_다시_쓰지_않는다(tmp_path: Path) -> None:
    expected = "/models/face/adaface/adaface_ir50_webface4m.onnx"
    docker_root, env_file = build_server(tmp_path, configured_path=expected)
    before = env_file.stat().st_mtime_ns

    changed = synchronize_face_recognizer_model_path(docker_root)

    assert changed is False
    assert env_file.stat().st_mtime_ns == before


def test_선택한_모델_파일이_없으면_env를_바꾸지_않는다(tmp_path: Path) -> None:
    docker_root, env_file = build_server(tmp_path)
    (docker_root / "models" / "face" / "adaface" / "adaface_ir50_webface4m.onnx").unlink()
    before = env_file.read_bytes()

    with pytest.raises(ValueError, match="모델 파일이 서버에 없습니다"):
        synchronize_face_recognizer_model_path(docker_root)

    assert env_file.read_bytes() == before


def test_지원하지_않는_인식기는_env를_바꾸지_않는다(tmp_path: Path) -> None:
    docker_root, env_file = build_server(tmp_path, recognizer="cosface")
    before = env_file.read_bytes()

    with pytest.raises(ValueError, match="arcface 또는 adaface"):
        synchronize_face_recognizer_model_path(docker_root)

    assert env_file.read_bytes() == before


def test_인식기_선택이_없으면_env를_바꾸지_않는다(tmp_path: Path) -> None:
    docker_root, env_file = build_server(tmp_path)
    env_file.write_text(
        env_file.read_text(encoding="utf-8").replace("FACE_RECOGNIZER=adaface\n", ""),
        encoding="utf-8",
    )
    before = env_file.read_bytes()

    with pytest.raises(ValueError, match="FACE_RECOGNIZER가 필요"):
        synchronize_face_recognizer_model_path(docker_root)

    assert env_file.read_bytes() == before


def test_경로_키가_중복이면_어느_줄도_바꾸지_않는다(tmp_path: Path) -> None:
    docker_root, env_file = build_server(tmp_path)
    env_file.write_text(
        env_file.read_text(encoding="utf-8")
        + "FACE_RECOGNITION_MODEL_PATH=/models/face/another.onnx\n",
        encoding="utf-8",
    )
    before = env_file.read_bytes()

    with pytest.raises(ValueError, match="중복"):
        synchronize_face_recognizer_model_path(docker_root)

    assert env_file.read_bytes() == before


def test_중복된_마지막_값이_정확해도_거부한다(tmp_path: Path) -> None:
    docker_root, env_file = build_server(tmp_path)
    env_file.write_text(
        env_file.read_text(encoding="utf-8")
        + "FACE_RECOGNITION_MODEL_PATH="
        "/models/face/adaface/adaface_ir50_webface4m.onnx\n",
        encoding="utf-8",
    )
    before = env_file.read_bytes()

    with pytest.raises(ValueError, match="중복"):
        synchronize_face_recognizer_model_path(docker_root)

    assert env_file.read_bytes() == before
