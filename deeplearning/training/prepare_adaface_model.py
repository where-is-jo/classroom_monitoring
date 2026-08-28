"""공식 CVLFace AdaFace IR50 WebFace4M을 고정 revision에서 ONNX로 변환한다.

가중치와 실행 코드는 Hugging Face의 AdaFace 저자 계정(``minchul``) 저장소를
사용한다. revision과 가중치 SHA-256을 모두 고정하며, CPU ONNX Runtime에서
동적 batch ``(N, 512)`` 출력까지 확인해야 완료된다.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

MODEL_REPOSITORY = "minchul/cvlface_adaface_ir50_webface4m"
MODEL_REVISION = "fe7718c6dc8af1b9946e0de778167c1695dd8814"
MODEL_VERSION = "cvlface-adaface-ir50-webface4m-fe7718c6"
MODEL_FILE_SHA256 = {
    "pretrained_model/model.pt": (
        "43bd2d570584d95d4a17ce81f26449034c45dbeed750afcab651872abc0e1496"
    ),
    "model.safetensors": (
        "da82e6e1dbe98ce23afb78283b858ad65e32484a2bac82e9adae298d04ff845d"
    ),
}
# 동일한 공식 source·고정 의존성의 ONNX 직렬화가 아래 두 바이트열로 재현됐다.
# 두 산출물 모두 467/467 가중치 로드와 CPU (N, 512) 실행 검증을 통과했다.
ONNX_FILE_SHA256_ALLOWLIST = frozenset(
    {
        "7baabf47c06391ab52e312134c6255846a5e55aa5a641f0553a89092fe2429d8",
        "7cb549232dd13071d9a12cd74cb9fa9741c9087021c1e6f1ae5ed994b5af7cfc",
    }
)
DEFAULT_MODEL_ROOT = Path(__file__).resolve().parents[1] / ".models" / "adaface"
DEFAULT_SOURCE_DIR = DEFAULT_MODEL_ROOT / "cvlface_ir50_webface4m"
DEFAULT_OUTPUT_PATH = DEFAULT_MODEL_ROOT / "adaface_ir50_webface4m.onnx"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_model(source_dir: Path) -> None:
    """고정 revision의 공식 코드·가중치를 내려받고 가중치 hash를 검증한다."""

    from huggingface_hub import hf_hub_download

    source_dir.mkdir(parents=True, exist_ok=True)
    files_path = Path(
        hf_hub_download(
            MODEL_REPOSITORY,
            "files.txt",
            revision=MODEL_REVISION,
            local_dir=source_dir,
        )
    )
    required_files = [
        value.strip()
        for value in files_path.read_text(encoding="utf-8").splitlines()
        if value.strip()
    ]
    required_files.extend(["config.json", "wrapper.py", "model.safetensors"])
    for filename in required_files:
        hf_hub_download(
            MODEL_REPOSITORY,
            filename,
            revision=MODEL_REVISION,
            local_dir=source_dir,
        )
    verify_model_files(source_dir)


def verify_model_files(source_dir: Path) -> None:
    for relative, expected in MODEL_FILE_SHA256.items():
        path = source_dir / relative
        if not path.is_file() or sha256_file(path) != expected:
            raise RuntimeError(f"공식 AdaFace 가중치 SHA-256이 다릅니다: {relative}")


@contextmanager
def _working_directory(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    path_value = str(path)
    sys.path.insert(0, path_value)
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)
        sys.path.remove(path_value)


def load_model(source_dir: Path):
    """고정된 로컬 snapshot만 사용해 공식 wrapper 모델을 CPU에 로드한다."""

    from transformers import AutoModel

    verify_model_files(source_dir)
    with _working_directory(source_dir):
        model = AutoModel.from_pretrained(
            source_dir,
            trust_remote_code=True,
            local_files_only=True,
        )
    model.cpu()
    model.eval()
    return model


def export_onnx(model, output_path: Path) -> None:
    import torch

    output_path.parent.mkdir(parents=True, exist_ok=True)
    dummy = torch.randn(1, 3, 112, 112, dtype=torch.float32)
    with torch.no_grad():
        sample = model(dummy)
    if not isinstance(sample, torch.Tensor) or sample.shape != (1, 512):
        raise RuntimeError(
            f"공식 AdaFace PyTorch 출력이 (1, 512)가 아닙니다: {sample.shape}"
        )
    torch.onnx.export(
        model,
        dummy,
        str(output_path),
        input_names=["input"],
        output_names=["embedding"],
        opset_version=18,
        dynamic_axes={"input": {0: "batch"}, "embedding": {0: "batch"}},
    )


def verify_onnx(output_path: Path) -> None:
    actual_sha256 = sha256_file(output_path)
    if actual_sha256 not in ONNX_FILE_SHA256_ALLOWLIST:
        raise RuntimeError(
            "AdaFace ONNX SHA-256이 검증된 산출물 목록에 없습니다. "
            f"actual={actual_sha256}"
        )

    import numpy as np
    import onnxruntime as ort

    session = ort.InferenceSession(str(output_path), providers=["CPUExecutionProvider"])
    inputs = session.get_inputs()
    outputs = session.get_outputs()
    if (
        len(inputs) != 1
        or len(outputs) != 1
        or tuple(inputs[0].shape[-3:]) != (3, 112, 112)
        or outputs[0].shape[-1] != 512
    ):
        raise RuntimeError("AdaFace ONNX 입출력 계약이 올바르지 않습니다.")
    dummy = np.random.default_rng(20260825).standard_normal(
        (2, 3, 112, 112), dtype=np.float32
    )
    (embedding,) = session.run([outputs[0].name], {inputs[0].name: dummy})
    if embedding.shape != (2, 512) or not np.isfinite(embedding).all():
        raise RuntimeError(
            f"CPU ONNX 출력이 유효한 (2, 512)가 아닙니다: {embedding.shape}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()

    print(f"1/4 공식 고정 revision 다운로드: {MODEL_REPOSITORY}@{MODEL_REVISION}")
    download_model(args.source_dir)
    print("2/4 가중치 SHA-256 검증 완료")
    model = load_model(args.source_dir)
    print("3/4 ONNX 변환")
    export_onnx(model, args.output)
    print("4/4 ONNX 허용 SHA-256 및 CPU (N, 512) 검증")
    verify_onnx(args.output)
    print(f"완료: {args.output}")
    print(
        "FACE_RECOGNIZER=adaface "
        f"FACE_RECOGNITION_MODEL_VERSION={MODEL_VERSION} "
        f"FACE_RECOGNITION_MODEL_PATH={args.output}"
    )


if __name__ == "__main__":
    main()
