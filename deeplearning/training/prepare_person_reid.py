"""공식 OSNet-AIN 가중치를 검증하고 ONNX 추론 모델로 변환한다."""

from __future__ import annotations

import argparse
import hashlib
import urllib.request
from pathlib import Path

import onnx
import torch

MODEL_FILE = (
    "osnet_ain_x1_0_msmt17_256x128_amsgrad_ep50_lr0.0015_coslr_"
    "b64_fb10_softmax_labsmth_flip_jitter.pth"
)
MODEL_URL = f"https://huggingface.co/kaiyangzhou/osnet/resolve/main/{MODEL_FILE}"
MODEL_SHA256 = "8a07e8da38946f7cee37f4561617bf8b6d2fe8f3a4027852893ea092e46d919f"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_weights(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".download")
    request = urllib.request.Request(
        MODEL_URL,
        headers={"User-Agent": "smart-office-monitoring-model-setup/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            with temporary.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
        actual = sha256(temporary)
        if actual != MODEL_SHA256:
            raise RuntimeError(f"OSNet 가중치 SHA-256 불일치: {actual}")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def export_onnx(weights_path: Path, output_path: Path) -> None:
    from torchreid.reid.models import build_model
    from torchreid.reid.utils import load_pretrained_weights

    model = build_model(
        name="osnet_ain_x1_0",
        num_classes=1,
        pretrained=False,
        use_gpu=False,
    )
    load_pretrained_weights(model, str(weights_path))
    model.eval()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    sample = torch.zeros((1, 3, 256, 128), dtype=torch.float32)
    with torch.inference_mode():
        torch.onnx.export(
            model,
            sample,
            str(output_path),
            input_names=["images"],
            output_names=["features"],
            dynamic_axes={"images": {0: "batch"}, "features": {0: "batch"}},
            opset_version=17,
            do_constant_folding=True,
            dynamo=False,
        )
    onnx.checker.check_model(onnx.load(str(output_path)))


def prepare(model_dir: Path | None = None, *, force: bool = False) -> Path:
    if model_dir is None:
        model_dir = Path(__file__).resolve().parents[1] / ".models/person_reid"
    weights_path = model_dir / MODEL_FILE
    output_path = model_dir / "osnet_ain_x1_0_msmt17.onnx"

    if force or not weights_path.is_file():
        print("공식 OSNet-AIN 가중치를 내려받는 중...")
        download_weights(weights_path)
    elif sha256(weights_path) != MODEL_SHA256:
        raise RuntimeError("기존 OSNet-AIN 가중치의 SHA-256이 다릅니다.")
    if force or not output_path.is_file():
        print("OSNet-AIN을 ONNX로 변환하는 중...")
        export_onnx(weights_path, output_path)
    print(f"OSNet-AIN ONNX 준비 완료: {output_path}")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    default_root = Path(__file__).resolve().parents[1] / ".models/person_reid"
    parser.add_argument("--model-dir", type=Path, default=default_root)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    prepare(args.model_dir, force=args.force)


if __name__ == "__main__":
    main()
