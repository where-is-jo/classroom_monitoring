"""AdaFace 공식 사전학습 checkpoint를 내려받아 ONNX로 변환한다.

팀 문서(FACE_IDENTIFICATION_TRACKING, 얼굴 디렉팅 개발 과정)의 결정대로,
현재 등록 학생 3명으로 backbone을 새로 fine-tuning하지 않고 공식 사전학습
가중치를 그대로 비교에 쓴다. 학습(training)이 아니라 "공식 checkpoint 준비"
스크립트다.

백본 구조(IR-50 등)를 직접 재구현하지 않고 공식 저장소
(https://github.com/mk-minchul/AdaFace) 의 net.py를 그대로 불러와 state_dict를
로드한다 — 레이어 하나만 달라도 state_dict가 조용히 잘못 매핑될 수 있어서다.

이 스크립트는 GPU(또는 CPU) 머신의 conda 환경에서 실행한다.
필요 패키지: `pip install torch gdown onnx onnxruntime`
(torch는 이미 사람 탐지 학습에 설치돼 있을 가능성이 높다.)

산출물은 deeplearning/.models/adaface/ 아래 저장되며, .models/는 이미
.gitignore 대상이라 커밋되지 않는다.

사용 예:
    python -m deeplearning.training.prepare_adaface_model
    python -m deeplearning.training.prepare_adaface_model --checkpoint ir50_ms1mv2
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# (architecture, google drive file id). 출처: AdaFace README pretrained models 표
# (https://github.com/mk-minchul/AdaFace, 2026-08-21 확인).
# ArcFace 기준 모델(w600k_r50)과 마찬가지로 R50 backbone인 조합을 기본값으로 쓴다.
CHECKPOINTS: dict[str, tuple[str, str]] = {
    "ir50_webface4m": ("ir_50", "1BmDRrhPsHSbXcWZoYFPJg2KJn1sd3QpN"),
    "ir50_ms1mv2": ("ir_50", "1eUaSHG4pGlIZK7hBkqjyp2fc2epKoBvI"),
    "ir50_casia": ("ir_50", "1g1qdg7_HSzkue7_VrW64fnWuHl0YL2C2"),
    "ir18_webface4m": ("ir_18", "1J17_QW1Oq00EhSWObISnhWEYr2NNrg2y"),
    "ir100_webface12m": ("ir_100", "1dswnavflETcnAuplZj1IOKKP0eM8ITgT"),
}

DEFAULT_MODEL_ROOT = Path(__file__).resolve().parents[1] / ".models" / "adaface"
DEFAULT_REPO_DIR = DEFAULT_MODEL_ROOT / "AdaFace_src"


def ensure_repo(repo_dir: Path) -> None:
    """공식 저장소의 net.py(백본 정의)를 확보한다. 이미 있으면 다시 받지 않는다."""
    if (repo_dir / "net.py").is_file():
        return
    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--depth", "1", "https://github.com/mk-minchul/AdaFace.git", str(repo_dir)],
        check=True,
    )


def download_checkpoint(file_id: str, destination: Path) -> None:
    if destination.is_file():
        print(f"이미 있음, 다시 받지 않음: {destination}")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    import gdown

    gdown.download(id=file_id, output=str(destination), quiet=False)
    if not destination.is_file():
        raise RuntimeError(f"checkpoint 다운로드에 실패했습니다: {destination}")


def load_backbone(repo_dir: Path, architecture: str, checkpoint_path: Path):
    import torch

    if str(repo_dir) not in sys.path:
        sys.path.insert(0, str(repo_dir))
    import net  # type: ignore[import-not-found]  # AdaFace 저장소의 net.py

    if not hasattr(net, "build_model"):
        raise RuntimeError(
            "AdaFace 저장소의 net.py에서 build_model을 찾지 못했습니다. "
            "공식 저장소 API가 바뀌었을 수 있으니 net.py를 직접 확인하세요: "
            f"{repo_dir / 'net.py'}"
        )
    model = net.build_model(architecture)
    state = torch.load(checkpoint_path, map_location="cpu")["state_dict"]
    model_state = {key[len("model.") :]: value for key, value in state.items() if key.startswith("model.")}
    if not model_state:
        raise RuntimeError(
            "checkpoint state_dict에서 'model.' 접두사가 붙은 키를 찾지 못했습니다. "
            "AdaFace 공식 checkpoint 형식이 바뀌었을 수 있습니다."
        )
    model.load_state_dict(model_state)
    model.eval()
    return model


def export_onnx(model, output_path: Path) -> None:
    import torch

    output_path.parent.mkdir(parents=True, exist_ok=True)
    dummy = torch.randn(1, 3, 112, 112, dtype=torch.float32)
    with torch.no_grad():
        sample_output = model(dummy)
    # AdaFace forward()는 공식 추론 예제 기준 (embedding, norm) 튜플을 반환한다.
    output_names = ["embedding", "norm"] if isinstance(sample_output, tuple) else ["embedding"]
    dynamic_axes = {"input": {0: "batch"}}
    for name in output_names:
        dynamic_axes[name] = {0: "batch"}
    torch.onnx.export(
        model,
        dummy,
        str(output_path),
        input_names=["input"],
        output_names=output_names,
        # opset 13을 요청하면 torch의 dynamo exporter가 18로 내보낸 뒤
        # 다운그레이드를 시도하다 실패하는 로그가 남는다(결과 자체는 정상
        # 폴백되지만 에러처럼 보인다). 18을 직접 요청해 그 노이즈를 없앤다.
        opset_version=18,
        dynamic_axes=dynamic_axes,
    )


def verify_onnx(output_path: Path) -> None:
    import numpy as np
    import onnxruntime as ort

    session = ort.InferenceSession(str(output_path), providers=["CPUExecutionProvider"])
    dummy = np.random.randn(2, 3, 112, 112).astype(np.float32)
    input_name = session.get_inputs()[0].name
    embedding_name = session.get_outputs()[0].name
    (embedding,) = session.run([embedding_name], {input_name: dummy})
    if embedding.shape != (2, 512):
        raise RuntimeError(f"기대한 (2, 512)가 아니라 {embedding.shape}가 나왔습니다.")
    print(f"ONNX 출력 shape 확인: embedding={embedding.shape}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", choices=sorted(CHECKPOINTS), default="ir50_webface4m")
    parser.add_argument("--repo-dir", type=Path, default=DEFAULT_REPO_DIR)
    parser.add_argument("--model-root", type=Path, default=DEFAULT_MODEL_ROOT)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    architecture, file_id = CHECKPOINTS[args.checkpoint]
    checkpoint_path = args.model_root / f"{args.checkpoint}.ckpt"
    output_path = args.output or (args.model_root / "adaface_ir50.onnx")

    print(f"1/4 공식 저장소(net.py) 확보: {args.repo_dir}")
    ensure_repo(args.repo_dir)
    print(f"2/4 checkpoint 다운로드({args.checkpoint}, architecture={architecture}): {checkpoint_path}")
    download_checkpoint(file_id, checkpoint_path)
    print("3/4 backbone 로드 및 ONNX 변환")
    model = load_backbone(args.repo_dir, architecture, checkpoint_path)
    export_onnx(model, output_path)
    print("4/4 ONNX 산출물 검증")
    verify_onnx(output_path)
    print(f"완료: {output_path}")
    print(f"FACE_RECOGNIZER=adaface FACE_RECOGNITION_MODEL_PATH={output_path} 로 실행하면 됩니다.")


if __name__ == "__main__":
    main()
