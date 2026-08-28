"""개인정보 없이 사람 탐지 원본을 짧게 기록하는 진단 trace.

모델이 NMS를 끝낸 결과를 ByteTrack이나 신원 인계가 바꾸기 전에 기록한다. 실제
카메라 ID와 촬영 시각은 파일에 쓰지 않고, 실행 중에만 ``source-N`` 별칭으로
치환한다. 이미지·얼굴·학생 식별자는 이 모듈의 입력 계약에도 없다.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import logging
import secrets
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from shared.types import CapturedFrame

from .consumer import ResultHandler
from .types import BBox, Detection, InferenceResult

logger = logging.getLogger(__name__)

TRACE_SCHEMA_VERSION = 1
DEFAULT_DUPLICATE_IOU_THRESHOLD = 0.50
DEFAULT_DUPLICATE_IOS_THRESHOLD = 0.85


def installed_ultralytics_version() -> str:
    """실행 이미지에 설치된 Ultralytics 버전을 반환한다."""

    try:
        return importlib.metadata.version("ultralytics")
    except importlib.metadata.PackageNotFoundError:
        # 실제 worker 이미지에는 필수 의존성이지만, 가중치 없는 단위 테스트에서는
        # 설치하지 않는다. trace metadata가 이 사실을 숨기지 않도록 명시한다.
        return "unavailable"


def _area(bbox: BBox) -> float:
    return float(max(0, bbox[2] - bbox[0]) * max(0, bbox[3] - bbox[1]))


def _intersection(left: BBox, right: BBox) -> float:
    width = max(0, min(left[2], right[2]) - max(left[0], right[0]))
    height = max(0, min(left[3], right[3]) - max(left[1], right[1]))
    return float(width * height)


def _overlap(left: BBox, right: BBox) -> tuple[float, float]:
    intersection = _intersection(left, right)
    left_area = _area(left)
    right_area = _area(right)
    union = left_area + right_area - intersection
    smaller = min(left_area, right_area)
    iou = 0.0 if union <= 0.0 else intersection / union
    ios = 0.0 if smaller <= 0.0 else intersection / smaller
    return iou, ios


def duplicate_group_ids(
    detections: tuple[Detection, ...],
    *,
    iou_threshold: float = DEFAULT_DUPLICATE_IOU_THRESHOLD,
    ios_threshold: float = DEFAULT_DUPLICATE_IOS_THRESHOLD,
) -> tuple[str | None, ...]:
    """겹친 사람 탐지를 결정적인 연결 성분 ID로 묶되 제거하지 않는다."""

    parents = list(range(len(detections)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[max(left_root, right_root)] = min(left_root, right_root)

    for left_index, left in enumerate(detections):
        for right_index in range(left_index + 1, len(detections)):
            iou, ios = _overlap(left.bbox, detections[right_index].bbox)
            if iou >= iou_threshold or ios >= ios_threshold:
                union(left_index, right_index)

    members_by_root: dict[int, list[int]] = {}
    for index in range(len(detections)):
        members_by_root.setdefault(find(index), []).append(index)
    duplicate_roots = sorted(
        (root for root, members in members_by_root.items() if len(members) > 1),
        key=lambda root: members_by_root[root][0],
    )
    labels = {root: f"duplicate-{order}" for order, root in enumerate(duplicate_roots, 1)}
    return tuple(labels.get(find(index)) for index in range(len(detections)))


class PersonDetectionTraceRecorder:
    """한 프로세스에서 최대 시간·프레임까지만 익명 JSONL을 기록한다."""

    def __init__(
        self,
        directory: Path,
        *,
        model_sha256: str | None,
        confidence_threshold: float,
        image_size: int,
        target_class_ids: dict[int, str],
        max_seconds: float = 600.0,
        max_frames: int = 3_000,
        retention_hours: float = 24.0,
        iou_threshold: float = DEFAULT_DUPLICATE_IOU_THRESHOLD,
        ios_threshold: float = DEFAULT_DUPLICATE_IOS_THRESHOLD,
        ultralytics_version: str | None = None,
        session_alias: str | None = None,
        file_token: str | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        self._directory = directory
        self._directory.mkdir(parents=True, exist_ok=True)
        self._max_seconds = max_seconds
        self._max_frames = max_frames
        self._retention_seconds = retention_hours * 3600.0
        self._iou_threshold = iou_threshold
        self._ios_threshold = ios_threshold
        self._monotonic = monotonic
        self._wall_clock = wall_clock
        self._started_at = monotonic()
        self._frame_count = 0
        self._source_aliases: dict[str, str] = {}
        self._disabled = False
        self._lock = threading.Lock()
        self._session_alias = session_alias or f"session-{secrets.token_hex(6)}"
        token = file_token or secrets.token_hex(8)
        self.path = directory / f"person-detections-{token}.jsonl"

        self._purge_expired_files()
        metadata = {
            "schema_version": TRACE_SCHEMA_VERSION,
            "record_type": "metadata",
            "session_alias": self._session_alias,
            "model_sha256": model_sha256,
            "ultralytics_version": (
                ultralytics_version or installed_ultralytics_version()
            ),
            "parameters": {
                "confidence_threshold": confidence_threshold,
                "image_size": image_size,
                "target_class_ids": {
                    str(class_id): class_name
                    for class_id, class_name in sorted(target_class_ids.items())
                },
                "duplicate_iou_threshold": iou_threshold,
                "duplicate_ios_threshold": ios_threshold,
            },
            "limits": {
                "max_seconds": max_seconds,
                "max_frames": max_frames,
                "retention_hours": retention_hours,
            },
        }
        self._append(metadata)
        # 실행이 계속되는 동안 현재 파일도 보존 시간이 지나면 지운다. 프로세스가
        # 멈춰 있었던 동안 지난 파일은 다음 기동의 _purge_expired_files가 지운다.
        self._retention_timer = threading.Timer(
            self._retention_seconds, self._expire_current_file
        )
        self._retention_timer.daemon = True
        self._retention_timer.start()

    @property
    def frame_count(self) -> int:
        return self._frame_count

    def _purge_expired_files(self) -> None:
        cutoff = self._wall_clock() - self._retention_seconds
        for candidate in self._directory.glob("person-detections-*.jsonl"):
            try:
                if candidate.stat().st_mtime < cutoff:
                    candidate.unlink()
            except FileNotFoundError:
                continue

    def _expire_current_file(self) -> None:
        with self._lock:
            self._disabled = True
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                logger.exception("보존 시간이 지난 익명 사람 탐지 trace를 지우지 못했다.")

    def _append(self, record: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8", newline="\n") as destination:
            destination.write(json.dumps(record, ensure_ascii=True, separators=(",", ":")))
            destination.write("\n")

    def record(self, captured: CapturedFrame, result: InferenceResult) -> None:
        """사람 탐지만 기록한다. 실패하면 이후 trace만 끄고 추론은 계속한다."""

        with self._lock:
            if self._disabled or self._frame_count >= self._max_frames:
                return
            elapsed_seconds = max(0.0, self._monotonic() - self._started_at)
            if elapsed_seconds > self._max_seconds:
                return

            people = tuple(
                detection
                for detection in result.detections
                if detection.class_name.casefold() == "person"
            )
            duplicate_groups = duplicate_group_ids(
                people,
                iou_threshold=self._iou_threshold,
                ios_threshold=self._ios_threshold,
            )
            source_alias = self._source_aliases.setdefault(
                captured.camera_id, f"source-{len(self._source_aliases) + 1}"
            )
            frame_index = self._frame_count + 1
            record = {
                "schema_version": TRACE_SCHEMA_VERSION,
                "record_type": "frame",
                "session_alias": self._session_alias,
                "source_alias": source_alias,
                "frame_index": frame_index,
                "elapsed_ms": int(round(elapsed_seconds * 1000)),
                "frame_shape": list(result.frame_shape),
                "person_detections": [
                    {
                        "object_id": f"object-{index}",
                        "bbox": list(detection.bbox),
                        "confidence": detection.confidence,
                        "duplicate_group": duplicate_group,
                    }
                    for index, (detection, duplicate_group) in enumerate(
                        zip(people, duplicate_groups, strict=True), 1
                    )
                ],
            }
            try:
                self._append(record)
            except OSError:
                self._disabled = True
                logger.exception(
                    "익명 사람 탐지 trace 쓰기를 중단한다. 추론은 계속한다."
                )
                return
            self._frame_count = frame_index


class PersonDetectionTraceHandler:
    """raw 결과를 먼저 기록한 뒤 기존 결과 체인을 그대로 호출한다."""

    def __init__(
        self, recorder: PersonDetectionTraceRecorder, *, inner: ResultHandler
    ) -> None:
        self._recorder = recorder
        self._inner = inner

    def __call__(self, captured: CapturedFrame, result: InferenceResult) -> None:
        self._recorder.record(captured, result)
        self._inner(captured, result)


def _read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"trace {line_number}번째 줄이 JSON이 아닙니다.") from error
            if not isinstance(record, dict):
                raise ValueError(f"trace {line_number}번째 줄은 객체여야 합니다.")
            yield record


def curate_person_detection_trace(
    source: Path, destination: Path, *, max_frames: int = 1_000
) -> int:
    """raw trace에서 허용 필드만 골라 재현 fixture를 만든다."""

    if source.resolve() == destination.resolve():
        raise ValueError("raw trace와 fixture 경로는 달라야 합니다.")
    records = _read_jsonl(source)
    try:
        metadata = next(records)
    except StopIteration as error:
        raise ValueError("trace가 비어 있습니다.") from error
    if metadata.get("record_type") != "metadata":
        raise ValueError("trace 첫 줄은 metadata여야 합니다.")

    parameters = metadata.get("parameters")
    limits = metadata.get("limits")
    if not isinstance(parameters, dict) or not isinstance(limits, dict):
        raise ValueError("trace metadata가 올바르지 않습니다.")
    curated: list[dict[str, Any]] = [
        {
            "schema_version": TRACE_SCHEMA_VERSION,
            "record_type": "metadata",
            "session_alias": metadata.get("session_alias"),
            "model_sha256": metadata.get("model_sha256"),
            "ultralytics_version": metadata.get("ultralytics_version"),
            "parameters": {
                key: parameters.get(key)
                for key in (
                    "confidence_threshold",
                    "image_size",
                    "target_class_ids",
                    "duplicate_iou_threshold",
                    "duplicate_ios_threshold",
                )
            },
            "limits": {
                "max_seconds": limits.get("max_seconds"),
                "max_frames": min(max_frames, int(limits.get("max_frames", max_frames))),
                "retention_hours": limits.get("retention_hours"),
            },
        }
    ]

    frame_count = 0
    for record in records:
        if frame_count >= max_frames:
            break
        if record.get("record_type") != "frame":
            raise ValueError("metadata 뒤에는 frame 레코드만 올 수 있습니다.")
        raw_detections = record.get("person_detections")
        if not isinstance(raw_detections, list):
            raise ValueError("frame의 person_detections는 목록이어야 합니다.")
        detections: list[dict[str, Any]] = []
        for detection in raw_detections:
            if not isinstance(detection, dict):
                raise ValueError("person detection은 객체여야 합니다.")
            detections.append(
                {
                    "object_id": detection.get("object_id"),
                    "bbox": detection.get("bbox"),
                    "confidence": detection.get("confidence"),
                    "duplicate_group": detection.get("duplicate_group"),
                }
            )
        curated.append(
            {
                "schema_version": TRACE_SCHEMA_VERSION,
                "record_type": "frame",
                "session_alias": record.get("session_alias"),
                "source_alias": record.get("source_alias"),
                "frame_index": record.get("frame_index"),
                "elapsed_ms": record.get("elapsed_ms"),
                "frame_shape": record.get("frame_shape"),
                "person_detections": detections,
            }
        )
        frame_count += 1

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="\n") as output:
        for record in curated:
            output.write(json.dumps(record, ensure_ascii=True, separators=(",", ":")))
            output.write("\n")
    return frame_count


def main() -> int:
    parser = argparse.ArgumentParser(
        description="익명 사람 탐지 raw trace를 테스트 fixture로 정리합니다."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--max-frames", type=int, default=1_000)
    args = parser.parse_args()
    if not 1 <= args.max_frames <= 1_000:
        parser.error("--max-frames는 1~1000이어야 합니다.")
    count = curate_person_detection_trace(
        args.source, args.destination, max_frames=args.max_frames
    )
    print(f"{count}개 frame을 익명 fixture로 저장했습니다: {args.destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
