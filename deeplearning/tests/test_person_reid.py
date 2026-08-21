from dataclasses import dataclass

import numpy as np

from deeplearning.person_reid import PersonReIdEngine, normalize_feature


@dataclass
class Input:
    name: str = "images"


class Session:
    def get_inputs(self):
        return [Input()]

    def get_providers(self):
        return ["CPUExecutionProvider"]

    def run(self, output_names, feed):
        assert feed["images"].shape == (1, 3, 256, 128)
        return [np.ones((1, 512), dtype=np.float32)]


def test_preprocess_and_extract_return_normalized_feature() -> None:
    engine = PersonReIdEngine("unused.onnx", session=Session())
    crop = np.full((300, 150, 3), 127, dtype=np.uint8)

    result = engine.extract(crop)

    assert result.shape == (512,)
    assert np.isclose(np.linalg.norm(result), 1.0)


def test_invalid_feature_dimension_is_rejected() -> None:
    try:
        normalize_feature(np.ones(10, dtype=np.float32))
    except ValueError as exc:
        assert "512차원" in str(exc)
    else:
        raise AssertionError("잘못된 특징 차원을 허용했습니다.")
