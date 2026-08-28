#!/usr/bin/env python3
"""서버 env의 얼굴 인식 모델 경로 한 줄을 선택 모델 계약에 맞춘다.

비밀 env 전체를 배포하거나 출력하지 않는다. ``FACE_RECOGNIZER``와 저장소가 정한
모델 상대 경로만 대조하고, 실제 ONNX가 서버에 있을 때만 경로 키 하나를 원자적으로
교체한다.
"""

from __future__ import annotations

import argparse
import os
import re
import stat
import sys
import tempfile
from pathlib import Path

from validate_face_handover_deployment import (
    FACE_MODEL_CONFIGS,
    host_model_path,
    read_env,
)

MODEL_PATH_KEY = "FACE_RECOGNITION_MODEL_PATH"
_ASSIGNMENT = re.compile(
    rf"^(?P<prefix>\s*(?:export\s+)?{MODEL_PATH_KEY}\s*=).*$"
)


def expected_model_path(recognizer: str) -> str:
    normalized = recognizer.strip().lower()
    config = FACE_MODEL_CONFIGS.get(normalized)
    if config is None:
        raise ValueError("FACE_RECOGNIZER는 arcface 또는 adaface여야 합니다.")
    relative = str(config["model_path"]).replace("\\", "/").lstrip("/")
    return f"/models/face/{relative}"


def _replace_assignment(content: str, expected: str) -> str:
    lines = content.splitlines(keepends=True)
    matching_indexes: list[int] = []
    for index, line in enumerate(lines):
        body = line.rstrip("\r\n")
        if _ASSIGNMENT.fullmatch(body):
            matching_indexes.append(index)
    if len(matching_indexes) > 1:
        raise ValueError(f"{MODEL_PATH_KEY}가 env 파일에 중복되어 있습니다.")

    if matching_indexes:
        index = matching_indexes[0]
        line = lines[index]
        body = line.rstrip("\r\n")
        newline = line[len(body) :]
        match = _ASSIGNMENT.fullmatch(body)
        assert match is not None
        lines[index] = f"{match.group('prefix')}{expected}{newline}"
        return "".join(lines)

    newline = "\r\n" if "\r\n" in content else "\n"
    if content and not content.endswith(("\n", "\r")):
        content += newline
    return f"{content}{MODEL_PATH_KEY}={expected}{newline}"


def _atomic_write(path: Path, content: str, *, has_utf8_bom: bool) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    descriptor, raw_temp_path = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp_path = Path(raw_temp_path)
    try:
        payload = content.encode("utf-8")
        if has_utf8_bom:
            payload = b"\xef\xbb\xbf" + payload
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(payload)
            destination.flush()
            os.fsync(destination.fileno())
        os.chmod(temp_path, mode)
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def synchronize_face_recognizer_model_path(
    docker_root: Path, env_file: Path | None = None
) -> bool:
    """필요하면 모델 경로만 바꾸고, 변경 여부를 반환한다."""

    env_path = env_file or docker_root / "env" / "deeplearning.dev.env"
    if not env_path.is_file():
        raise ValueError("deeplearning.dev.env 파일이 없습니다.")
    values = read_env(env_path)
    recognizer = values.get("FACE_RECOGNIZER", "").strip()
    if not recognizer:
        raise ValueError("deeplearning.dev.env에 FACE_RECOGNIZER가 필요합니다.")
    expected = expected_model_path(recognizer)
    model_file = host_model_path(docker_root, expected)
    if not model_file.is_file():
        raise ValueError(
            "선택한 얼굴 인식 모델 파일이 서버에 없습니다: "
            f"{model_file.relative_to(docker_root)}"
        )
    raw = env_path.read_bytes()
    has_utf8_bom = raw.startswith(b"\xef\xbb\xbf")
    content = raw.decode("utf-8-sig")
    # 이미 값이 맞더라도 중복 키는 먼저 거부한다. env parser는 마지막 값을 쓰므로
    # 중복을 그대로 두면 사람이 보는 줄과 실제 적용 값이 달라질 수 있다.
    updated = _replace_assignment(content, expected)
    if values.get(MODEL_PATH_KEY, "").strip() == expected:
        print(f"얼굴 인식 모델 경로가 이미 {recognizer.strip().lower()} 계약과 일치합니다.")
        return False

    _atomic_write(env_path, updated, has_utf8_bom=has_utf8_bom)
    print(
        "얼굴 인식 모델 경로 한 줄을 선택 계약에 맞췄습니다: "
        f"{recognizer.strip().lower()} -> {expected}"
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="GPU 서버 얼굴 인식 모델 경로를 선택 모델 계약에 맞춥니다."
    )
    parser.add_argument("--docker-root", type=Path, default=Path(".docker"))
    args = parser.parse_args()
    try:
        synchronize_face_recognizer_model_path(args.docker_root)
    except (OSError, UnicodeError, ValueError) as error:
        print(f"얼굴 인식 모델 경로 정렬 실패: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
