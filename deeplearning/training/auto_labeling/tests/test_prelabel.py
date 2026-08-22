from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import cv2
import numpy as np
import pytest

from auto_labeling.core import load_settings, sha256_file
from auto_labeling.errors import AutoLabelingError
from auto_labeling.prelabel import (
    UltralyticsPredictor,
    _find_person_class_id,
    run_prelabel,
)


class FakeTensor:
    def __init__(self, values: list[Any]) -> None:
        self._values = values

    def cpu(self) -> FakeTensor:
        return self

    def tolist(self) -> list[Any]:
        return self._values


class FakeBoxes:
    def __init__(
        self,
        *,
        xyxy: list[list[float]],
        confidence: list[float],
        classes: list[float],
    ) -> None:
        self.xyxy = FakeTensor(xyxy)
        self.conf = FakeTensor(confidence)
        self.cls = FakeTensor(classes)


class FakeResult:
    def __init__(self, boxes: FakeBoxes | None, *, shape: tuple[int, int]) -> None:
        self.boxes = boxes
        self.orig_shape = shape


class FakeModel:
    def __init__(self, results: list[FakeResult], *, names: object) -> None:
        self.names = names
        self._results = results
        self.calls: list[dict[str, object]] = []

    def predict(self, **kwargs: object) -> list[FakeResult]:
        self.calls.append(kwargs)
        return self._results


def _install_fake_ultralytics(
    monkeypatch: pytest.MonkeyPatch, model: FakeModel
) -> list[str]:
    loaded_paths: list[str] = []

    def load_model(path: str) -> FakeModel:
        loaded_paths.append(path)
        return model

    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=load_model))
    return loaded_paths


def test_ultralytics_predictor_keeps_person_and_normalizes_clipped_box(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = FakeModel(
        [
            FakeResult(
                FakeBoxes(
                    xyxy=[[-10.0, 5.0, 110.0, 95.0], [10.0, 20.0, 30.0, 40.0]],
                    confidence=[0.92, 0.88],
                    classes=[0.0, 1.0],
                ),
                shape=(100, 100),
            )
        ],
        names={0: "person", 1: "bicycle"},
    )
    loaded_paths = _install_fake_ultralytics(monkeypatch, model)
    model_path = tmp_path / "yolov8n.pt"
    image_path = tmp_path / "frame.jpg"

    predictor = UltralyticsPredictor(
        model_path, confidence_threshold=0.25, device="cpu"
    )
    candidates = predictor.predict(image_path)

    assert loaded_paths == [str(model_path)]
    assert model.calls == [
        {
            "source": str(image_path),
            "conf": 0.25,
            "device": "cpu",
            "verbose": False,
        }
    ]
    assert len(candidates) == 1
    assert candidates[0].class_id == 0
    assert candidates[0].class_name == "person"
    assert candidates[0].confidence == 0.92
    assert candidates[0].bbox_xyxy_pixels == (0.0, 5.0, 100.0, 95.0)
    assert candidates[0].bbox_yolo == (0.5, 0.5, 1.0, 0.9)


def test_ultralytics_predictor_supports_empty_boxes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = FakeModel([FakeResult(None, shape=(80, 120))], names=["person"])
    _install_fake_ultralytics(monkeypatch, model)
    predictor = UltralyticsPredictor(
        tmp_path / "yolov8n.pt", confidence_threshold=0.25, device="cpu"
    )

    assert predictor.predict(tmp_path / "empty.jpg") == []


def test_ultralytics_predictor_converts_runtime_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FailingModel(FakeModel):
        def predict(self, **kwargs: object) -> list[FakeResult]:
            raise RuntimeError("device failure")

    model = FailingModel([], names={0: "person"})
    _install_fake_ultralytics(monkeypatch, model)
    predictor = UltralyticsPredictor(
        tmp_path / "yolov8n.pt", confidence_threshold=0.25, device="cpu"
    )

    with pytest.raises(AutoLabelingError, match="후보 bbox 추론"):
        predictor.predict(tmp_path / "frame.jpg")


@pytest.mark.parametrize(
    ("names", "expected"),
    [({0: "person", 1: "car"}, 0), (["car", "person"], 1)],
)
def test_find_person_class_id_supports_ultralytics_name_shapes(
    names: object, expected: int
) -> None:
    assert _find_person_class_id(names) == expected


@pytest.mark.parametrize("names", [{0: "car"}, ["car"], {0: "person", 1: "person"}])
def test_find_person_class_id_rejects_missing_or_duplicate_person(
    names: object,
) -> None:
    with pytest.raises(AutoLabelingError, match="person을 하나만"):
        _find_person_class_id(names)


def test_run_prelabel_accepts_yolo11n(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model_path = tmp_path / "yolo11n.pt"
    model_path.write_bytes(b"weights")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    expected = run_dir / "candidate-labels"

    monkeypatch.setitem(
        sys.modules,
        "ultralytics",
        SimpleNamespace(__version__="test"),
    )
    monkeypatch.setattr(
        "auto_labeling.prelabel.UltralyticsPredictor",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        "auto_labeling.prelabel.generate_candidate_labels",
        lambda *_args, **_kwargs: expected,
    )

    result = run_prelabel(
        run_dir,
        model_path,
        load_settings(),
        device="cpu",
    )

    assert result == expected


def test_run_prelabel_accepts_verified_n1_best_weight(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model_path = tmp_path / "best.pt"
    model_path.write_bytes(b"n1-weights")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    expected = run_dir / "candidate-labels"
    predictor_calls: list[dict[str, object]] = []

    monkeypatch.setitem(
        sys.modules,
        "ultralytics",
        SimpleNamespace(__version__="test"),
    )

    def predictor(*_args: object, **kwargs: object) -> object:
        predictor_calls.append(kwargs)
        return object()

    monkeypatch.setattr("auto_labeling.prelabel.UltralyticsPredictor", predictor)
    monkeypatch.setattr(
        "auto_labeling.prelabel.generate_candidate_labels",
        lambda *_args, **_kwargs: expected,
    )
    preprocessing = {
        "method": "uniform-full-frame-pixelation-v1",
        "pixelation_block_size": 8,
    }

    result = run_prelabel(
        run_dir,
        model_path,
        load_settings(),
        device="cpu",
        expected_model_sha256=sha256_file(model_path),
        input_preprocessing=preprocessing,
    )

    assert result == expected
    assert predictor_calls[0]["input_preprocessing"] == preprocessing


def test_run_prelabel_rejects_wrong_n1_hash(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "best.pt"
    model_path.write_bytes(b"unexpected-weights")
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    with pytest.raises(AutoLabelingError, match="N1 계약"):
        run_prelabel(
            run_dir,
            model_path,
            load_settings(),
            device="cpu",
            expected_model_sha256="0" * 64,
        )


def test_ultralytics_predictor_applies_input_preprocessing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    model = FakeModel([FakeResult(None, shape=(16, 16))], names=["person"])
    _install_fake_ultralytics(monkeypatch, model)
    image_path = tmp_path / "frame.jpg"
    image = np.arange(16 * 16 * 3, dtype=np.uint8).reshape(16, 16, 3)
    assert cv2.imwrite(str(image_path), image)
    predictor = UltralyticsPredictor(
        tmp_path / "best.pt",
        confidence_threshold=0.25,
        device="cpu",
        input_preprocessing={
            "method": "uniform-full-frame-pixelation-v1",
            "pixelation_block_size": 8,
        },
    )

    assert predictor.predict(image_path) == []
    source = model.calls[0]["source"]
    assert isinstance(source, np.ndarray)
    assert source.shape == image.shape
    assert not np.array_equal(source, image)
