"""익명 사람 탐지 trace의 결정성·개인정보·상한 검증."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from shared.types import CapturedFrame

from ..config import InferenceSettings
from ..detection_trace import (
    PersonDetectionTraceHandler,
    PersonDetectionTraceRecorder,
    curate_person_detection_trace,
    duplicate_group_ids,
)
from ..types import Detection, InferenceResult


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def captured(*, camera_id: str = "real-camera-secret", sequence: int = 0) -> CapturedFrame:
    return CapturedFrame(
        camera_id=camera_id,
        frame=np.zeros((100, 200, 3), dtype=np.uint8),
        captured_at=datetime(2026, 8, 28, 9, 30, tzinfo=UTC),
        sequence=sequence,
    )


def result_with_duplicate() -> InferenceResult:
    return InferenceResult(
        frame_shape=(100, 200, 3),
        detections=(
            Detection(
                class_id=0,
                class_name="person",
                confidence=0.91,
                bbox=(10, 10, 80, 95),
                student_id="student-secret",
                face_bbox=(20, 20, 40, 40),
                track_id="person-secret",
            ),
            Detection(
                class_id=0,
                class_name="person",
                confidence=0.74,
                bbox=(12, 12, 78, 94),
            ),
            Detection(
                class_id=67,
                class_name="cell phone",
                confidence=0.80,
                bbox=(150, 20, 180, 60),
            ),
        ),
    )


def read_records(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def make_recorder(tmp_path: Path, **overrides: object) -> PersonDetectionTraceRecorder:
    defaults: dict[str, object] = {
        "model_sha256": "a" * 64,
        "confidence_threshold": 0.25,
        "image_size": 1280,
        "target_class_ids": {0: "person", 67: "cell phone"},
        "session_alias": "session-test",
        "file_token": "test",
        "ultralytics_version": "8.4.123",
    }
    defaults.update(overrides)
    return PersonDetectionTraceRecorder(tmp_path, **defaults)  # type: ignore[arg-type]


def test_trace는_실제_카메라와_신원_정보를_쓰지_않는다(tmp_path: Path) -> None:
    recorder = make_recorder(tmp_path)
    handled: list[InferenceResult] = []
    handler = PersonDetectionTraceHandler(
        recorder, inner=lambda _captured, result: handled.append(result)
    )

    raw_result = result_with_duplicate()
    handler(captured(), raw_result)

    text = recorder.path.read_text(encoding="utf-8")
    assert "real-camera-secret" not in text
    assert "student-secret" not in text
    assert "person-secret" not in text
    assert "captured_at" not in text
    records = read_records(recorder.path)
    assert records[0]["model_sha256"] == "a" * 64
    frame = records[1]
    assert frame["source_alias"] == "source-1"
    assert len(frame["person_detections"]) == 2  # type: ignore[arg-type]
    detections = frame["person_detections"]  # type: ignore[assignment]
    assert detections[0]["object_id"] == "object-1"  # type: ignore[index]
    assert detections[0]["duplicate_group"] == "duplicate-1"  # type: ignore[index]
    assert detections[1]["duplicate_group"] == "duplicate-1"  # type: ignore[index]
    assert handled == [raw_result], "trace가 기존 결과를 바꾸면 안 된다"


def test_중복_그룹은_입력_순서가_같으면_항상_같다() -> None:
    people = result_with_duplicate().detections[:2]

    assert duplicate_group_ids(people) == ("duplicate-1", "duplicate-1")
    assert duplicate_group_ids(people) == ("duplicate-1", "duplicate-1")


def test_trace는_프레임과_시간_상한에서_멈춘다(tmp_path: Path) -> None:
    clock = Clock()
    recorder = make_recorder(tmp_path, max_frames=2, max_seconds=10, monotonic=clock)

    recorder.record(captured(sequence=1), result_with_duplicate())
    clock.value = 9
    recorder.record(captured(sequence=2), result_with_duplicate())
    clock.value = 10
    recorder.record(captured(sequence=3), result_with_duplicate())

    assert recorder.frame_count == 2
    assert len(read_records(recorder.path)) == 3  # metadata + 2 frames

    time_limited = make_recorder(
        tmp_path / "time", max_frames=10, max_seconds=5, monotonic=clock
    )
    clock.value = 16
    time_limited.record(captured(), result_with_duplicate())
    assert time_limited.frame_count == 0


def test_trace_쓰기_실패가_기존_결과_처리를_막지_않는다(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = make_recorder(tmp_path)

    def fail_to_append(_record: object) -> None:
        raise OSError("volume full")

    monkeypatch.setattr(recorder, "_append", fail_to_append)
    handled: list[InferenceResult] = []
    handler = PersonDetectionTraceHandler(
        recorder, inner=lambda _captured, result: handled.append(result)
    )
    raw_result = result_with_duplicate()

    handler(captured(), raw_result)

    assert recorder.frame_count == 0
    assert handled == [raw_result]


def test_기동할_때_24시간이_지난_raw_trace를_지운다(tmp_path: Path) -> None:
    expired = tmp_path / "person-detections-expired.jsonl"
    expired.write_text("{}\n", encoding="utf-8")
    os.utime(expired, (1.0, 1.0))

    make_recorder(tmp_path, wall_clock=lambda: 24 * 3600 + 2)

    assert not expired.exists()


def test_fixture_정리기는_허용하지_않은_필드를_버린다(tmp_path: Path) -> None:
    recorder = make_recorder(tmp_path)
    recorder.record(captured(), result_with_duplicate())
    records = read_records(recorder.path)
    records[1]["camera_id"] = "camera-secret"
    detections = records[1]["person_detections"]  # type: ignore[assignment]
    detections[0]["student_id"] = "student-secret"  # type: ignore[index]
    recorder.path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
    )
    fixture = tmp_path / "fixture.jsonl"

    count = curate_person_detection_trace(recorder.path, fixture, max_frames=1)

    text = fixture.read_text(encoding="utf-8")
    assert count == 1
    assert "camera-secret" not in text
    assert "student-secret" not in text


def test_커밋된_fixture는_익명이고_중복을_재현한다() -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "person_detection_trace.jsonl"
    text = fixture.read_text(encoding="utf-8")
    records = read_records(fixture)

    assert "camera_id" not in text
    assert "student_id" not in text
    assert "face" not in text.casefold()
    assert len(records) <= 1_001
    detections = records[1]["person_detections"]  # type: ignore[assignment]
    assert [item["duplicate_group"] for item in detections] == [  # type: ignore[index]
        "duplicate-1",
        "duplicate-1",
    ]


def test_trace_기본값은_꺼져_있고_환경변수로만_켤_수_있다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PERSON_DETECTION_TRACE_ENABLED", raising=False)
    settings = InferenceSettings(_env_file=None)  # type: ignore[call-arg]
    assert settings.person_detection_trace_enabled is False

    monkeypatch.setenv("PERSON_DETECTION_TRACE_ENABLED", "true")
    enabled = InferenceSettings(_env_file=None)  # type: ignore[call-arg]
    assert enabled.person_detection_trace_enabled is True
