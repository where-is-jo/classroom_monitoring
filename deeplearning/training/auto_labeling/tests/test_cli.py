from __future__ import annotations

import json
from pathlib import Path

import pytest

from auto_labeling import cli
from auto_labeling.errors import AutoLabelingError


@pytest.mark.parametrize(
    "arguments",
    [
        ["prepare", "--manifest", "input.json"],
        [
            "prelabel",
            "--run-dir",
            "run",
            "--model-path",
            "yolov8n.pt",
            "--device",
            "cpu",
        ],
        ["prepare-review", "--run-dir", "run"],
        [
            "review-complete",
            "--review-dir",
            "review",
            "--reviewer-id",
            "reviewer-001",
            "--labelimg-executable",
            "labelImg.exe",
            "--confirm-labelimg-smoke",
        ],
        ["calibrate", "--run-dir", "run", "--review-dir", "review"],
        ["publish", "--run-dir", "run"],
        ["validate", "--dataset-dir", "dataset"],
    ],
)
def test_parser_accepts_every_public_command(arguments: list[str]) -> None:
    parsed = cli.build_parser().parse_args(arguments)

    assert parsed.command == arguments[0]


@pytest.mark.parametrize(
    ("arguments", "target_name", "return_value", "expected_status"),
    [
        (
            ["prepare", "--manifest", "input.json"],
            "prepare_run",
            Path("run"),
            "prepared",
        ),
        (
            ["prelabel", "--run-dir", "run", "--model-path", "yolov8n.pt"],
            "run_prelabel",
            Path("labels"),
            "prelabeled",
        ),
        (
            ["prepare-review", "--run-dir", "run"],
            "prepare_review",
            Path("review"),
            "review-prepared",
        ),
        (
            [
                "review-complete",
                "--review-dir",
                "review",
                "--reviewer-id",
                "reviewer-001",
                "--labelimg-executable",
                "labelImg.exe",
                "--confirm-labelimg-smoke",
            ],
            "complete_review",
            Path("review-completed.json"),
            "review-completed",
        ),
        (
            ["calibrate", "--run-dir", "run", "--review-dir", "review"],
            "create_calibration",
            Path("calibration.json"),
            "calibrated",
        ),
        (
            ["publish", "--run-dir", "run"],
            "publish_dataset",
            Path("dataset"),
            "published",
        ),
        (
            ["validate", "--dataset-dir", "dataset"],
            "validate_dataset",
            {"frame_count": 3},
            "valid",
        ),
    ],
)
def test_main_dispatches_every_public_command(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: list[str],
    target_name: str,
    return_value: object,
    expected_status: str,
) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def target(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        return return_value

    monkeypatch.setattr(cli, "load_settings", lambda: object())
    monkeypatch.setattr(cli, target_name, target)
    monkeypatch.setattr(
        cli,
        "read_json",
        lambda path: (
            {"frame_ids": ["frame-001"], "auto_accepted_frame_ids": []}
            if Path(path).name == "review-batch.json"
            else {"quality_gate": {"passed": True}}
        ),
    )

    exit_code = cli.main(arguments)
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert calls
    assert output["status"] == expected_status


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (AutoLabelingError("계약 오류"), "계약 오류"),
        (OSError("private filesystem detail"), "파일 작업을 완료할 수 없습니다"),
    ],
)
def test_main_returns_stable_error_contract(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
    message: str,
) -> None:
    def fail() -> object:
        raise error

    monkeypatch.setattr(cli, "load_settings", fail)

    exit_code = cli.main(["validate", "--dataset-dir", "dataset"])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.out == ""
    assert message in captured.err
