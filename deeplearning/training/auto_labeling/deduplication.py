from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import cv2
import numpy as np
from numpy.typing import NDArray

from .core import Settings, sha256_file
from .errors import AutoLabelingError
from .yolo import YoloBox, iou, parse_yolo_file


@dataclass(frozen=True)
class DeduplicationInput:
    frame_id: str
    camera_id: str
    session_id: str
    image_path: Path
    label_path: Path
    image_sha256: str
    approval_type: str


@dataclass(frozen=True)
class DeduplicationResult:
    retained_frame_ids: tuple[str, ...]
    group_id_by_representative: dict[str, str]
    report_rows: tuple[dict[str, Any], ...]
    input_frame_count: int
    retained_frame_count: int
    removed_frame_count: int


@dataclass(frozen=True)
class _FrameFeatures:
    source: DeduplicationInput
    boxes: tuple[YoloBox, ...]
    phash: int
    grayscale: NDArray[np.float32]
    sharpness: float


@dataclass(frozen=True)
class _DuplicateRelation:
    duplicate: _FrameFeatures
    matched_against: _FrameFeatures
    match_type: str
    phash_hamming_distance: int
    pixel_mae: float
    bbox_min_iou: float


@dataclass
class _GroupState:
    representative: _FrameFeatures
    duplicates: list[_DuplicateRelation] = field(default_factory=list)
    camera_anchors: dict[str, _FrameFeatures] = field(default_factory=dict)


@dataclass(frozen=True)
class _Anchor:
    group: _GroupState
    frame: _FrameFeatures


@dataclass
class _BKNode:
    value: int
    anchors: list[_Anchor] = field(default_factory=list)
    children: dict[int, _BKNode] = field(default_factory=dict)


class _BKTree:
    def __init__(self) -> None:
        self._root: _BKNode | None = None

    def insert(self, value: int, anchor: _Anchor) -> None:
        if self._root is None:
            self._root = _BKNode(value=value, anchors=[anchor])
            return
        node = self._root
        while True:
            distance = _hamming_distance(value, node.value)
            if distance == 0:
                node.anchors.append(anchor)
                return
            child = node.children.get(distance)
            if child is None:
                node.children[distance] = _BKNode(value=value, anchors=[anchor])
                return
            node = child

    def query(self, value: int, radius: int) -> list[_Anchor]:
        if self._root is None:
            return []
        matches: list[_Anchor] = []
        pending = [self._root]
        while pending:
            node = pending.pop()
            distance = _hamming_distance(value, node.value)
            if distance <= radius:
                matches.extend(node.anchors)
            minimum = max(0, distance - radius)
            maximum = distance + radius
            pending.extend(
                child
                for edge, child in node.children.items()
                if minimum <= edge <= maximum
            )
        return matches


def deduplication_policy(settings: Settings) -> dict[str, object]:
    return {
        "policy_version": settings.deduplication_policy_version,
        "exact_scope": "all-cameras",
        "visual_scope": "same-camera",
        "phash_algorithm": "dct-64",
        "phash_hamming_threshold": settings.duplicate_phash_hamming_threshold,
        "comparison_size": settings.duplicate_comparison_size,
        "pixel_mae_threshold": settings.duplicate_pixel_mae_threshold,
        "bbox_iou_threshold": settings.duplicate_bbox_iou_threshold,
        "representative_policy": "human-reviewed-then-sharpness-then-frame-id",
    }


def deduplicate_frames(
    inputs: list[DeduplicationInput], settings: Settings
) -> DeduplicationResult:
    if not inputs:
        raise AutoLabelingError("중복 제거 대상 프레임이 없습니다.")
    frame_ids = [item.frame_id for item in inputs]
    if len(frame_ids) != len(set(frame_ids)):
        raise AutoLabelingError("중복 제거 입력에 같은 frame_id가 여러 번 있습니다.")

    features = [_load_features(item, settings) for item in inputs]
    features.sort(key=_representative_priority)
    groups: list[_GroupState] = []
    exact_index: dict[str, tuple[_FrameFeatures, _GroupState]] = {}
    camera_indexes: dict[str, _BKTree] = {}

    for frame in features:
        exact_match = exact_index.get(frame.source.image_sha256)
        if exact_match is not None:
            matched_frame, group = exact_match
            bbox_min_iou = _perfect_bbox_match(
                frame.boxes, matched_frame.boxes, 1.0 - 1e-8
            )
            if bbox_min_iou is None:
                raise AutoLabelingError(
                    "완전히 같은 이미지에 서로 다른 검수 라벨이 있습니다: "
                    f"{matched_frame.source.frame_id}, {frame.source.frame_id}"
                )
            group.duplicates.append(
                _DuplicateRelation(
                    duplicate=frame,
                    matched_against=matched_frame,
                    match_type="exact-sha256",
                    phash_hamming_distance=0,
                    pixel_mae=0.0,
                    bbox_min_iou=bbox_min_iou,
                )
            )
            _register_camera_anchor(group, frame, camera_indexes)
            continue

        visual_match = _find_visual_match(frame, camera_indexes, settings)
        if visual_match is None:
            group = _GroupState(
                representative=frame,
                camera_anchors={frame.source.camera_id: frame},
            )
            groups.append(group)
            camera_indexes.setdefault(frame.source.camera_id, _BKTree()).insert(
                frame.phash, _Anchor(group=group, frame=frame)
            )
        else:
            anchor, phash_distance, pixel_mae, bbox_min_iou = visual_match
            group = anchor.group
            group.duplicates.append(
                _DuplicateRelation(
                    duplicate=frame,
                    matched_against=anchor.frame,
                    match_type="visual-same-camera",
                    phash_hamming_distance=phash_distance,
                    pixel_mae=pixel_mae,
                    bbox_min_iou=bbox_min_iou,
                )
            )
        exact_index[frame.source.image_sha256] = (frame, group)

    retained_frame_ids = tuple(
        sorted(group.representative.source.frame_id for group in groups)
    )
    report_rows: list[dict[str, Any]] = []
    group_id_by_representative: dict[str, str] = {}
    removed_count = 0
    for group in groups:
        if not group.duplicates:
            continue
        member_ids = [
            group.representative.source.frame_id,
            *(relation.duplicate.source.frame_id for relation in group.duplicates),
        ]
        group_id = duplicate_group_id(settings.deduplication_policy_version, member_ids)
        group_id_by_representative[group.representative.source.frame_id] = group_id
        removed_count += len(group.duplicates)
        report_rows.append(_group_report(group_id, group))
    report_rows.sort(key=lambda row: str(row["group_id"]))

    return DeduplicationResult(
        retained_frame_ids=retained_frame_ids,
        group_id_by_representative=group_id_by_representative,
        report_rows=tuple(report_rows),
        input_frame_count=len(features),
        retained_frame_count=len(retained_frame_ids),
        removed_frame_count=removed_count,
    )


def _load_features(item: DeduplicationInput, settings: Settings) -> _FrameFeatures:
    if item.approval_type not in {"human-reviewed", "calibrated-auto-accept"}:
        raise AutoLabelingError("중복 제거 입력의 승인 유형이 올바르지 않습니다.")
    if sha256_file(item.image_path) != item.image_sha256:
        raise AutoLabelingError(
            f"frame_id={item.frame_id}: 중복 비교 이미지 해시가 다릅니다."
        )
    grayscale = cv2.imread(str(item.image_path), cv2.IMREAD_GRAYSCALE)
    if grayscale is None:
        raise AutoLabelingError(
            f"frame_id={item.frame_id}: 중복 비교 이미지를 열 수 없습니다."
        )
    grayscale_image = cast(NDArray[np.uint8], grayscale)
    comparison = cv2.resize(
        grayscale_image,
        (settings.duplicate_comparison_size, settings.duplicate_comparison_size),
        interpolation=cv2.INTER_AREA,
    ).astype(np.float32)
    comparison /= 255.0
    sharpness = float(cv2.Laplacian(grayscale_image, cv2.CV_64F).var())
    boxes = tuple(parse_yolo_file(item.label_path))
    return _FrameFeatures(
        source=item,
        boxes=boxes,
        phash=_perceptual_hash(grayscale_image),
        grayscale=comparison,
        sharpness=sharpness,
    )


def _perceptual_hash(grayscale: NDArray[np.uint8]) -> int:
    resized = cv2.resize(grayscale, (32, 32), interpolation=cv2.INTER_AREA).astype(
        np.float32
    )
    coefficients = cv2.dct(resized)[:8, :8].reshape(-1)
    median = float(np.median(coefficients[1:]))
    value = 0
    for coefficient in coefficients:
        value = (value << 1) | int(float(coefficient) > median)
    return value


def _representative_priority(frame: _FrameFeatures) -> tuple[int, float, str]:
    human_priority = 0 if frame.source.approval_type == "human-reviewed" else 1
    return human_priority, -frame.sharpness, frame.source.frame_id


def _find_visual_match(
    frame: _FrameFeatures,
    camera_indexes: dict[str, _BKTree],
    settings: Settings,
) -> tuple[_Anchor, int, float, float] | None:
    camera_index = camera_indexes.get(frame.source.camera_id)
    if camera_index is None:
        return None
    matches: list[tuple[int, float, float, str, _Anchor]] = []
    for anchor in camera_index.query(
        frame.phash, settings.duplicate_phash_hamming_threshold
    ):
        phash_distance = _hamming_distance(frame.phash, anchor.frame.phash)
        pixel_mae = float(np.mean(np.abs(frame.grayscale - anchor.frame.grayscale)))
        if pixel_mae > settings.duplicate_pixel_mae_threshold:
            continue
        bbox_min_iou = _perfect_bbox_match(
            frame.boxes,
            anchor.frame.boxes,
            settings.duplicate_bbox_iou_threshold,
        )
        if bbox_min_iou is None:
            continue
        matches.append(
            (
                phash_distance,
                pixel_mae,
                -bbox_min_iou,
                anchor.frame.source.frame_id,
                anchor,
            )
        )
    if not matches:
        return None
    phash_distance, pixel_mae, negative_bbox_iou, _, anchor = min(
        matches, key=lambda value: value[:4]
    )
    return anchor, phash_distance, pixel_mae, -negative_bbox_iou


def _perfect_bbox_match(
    first: tuple[YoloBox, ...], second: tuple[YoloBox, ...], threshold: float
) -> float | None:
    if len(first) != len(second):
        return None
    if not first:
        return 1.0
    candidates = [
        sorted(
            (
                (iou(first_box, second_box), second_index)
                for second_index, second_box in enumerate(second)
                if first_box.class_id == second_box.class_id
                and iou(first_box, second_box) >= threshold
            ),
            reverse=True,
        )
        for first_box in first
    ]
    assigned_first_by_second: dict[int, int] = {}

    def assign(first_index: int, visited_seconds: set[int]) -> bool:
        for _, second_index in candidates[first_index]:
            if second_index in visited_seconds:
                continue
            visited_seconds.add(second_index)
            previous_first = assigned_first_by_second.get(second_index)
            if previous_first is None or assign(previous_first, visited_seconds):
                assigned_first_by_second[second_index] = first_index
                return True
        return False

    for first_index in range(len(first)):
        if not assign(first_index, set()):
            return None
    matched_ious = [
        iou(first[first_index], second[second_index])
        for second_index, first_index in assigned_first_by_second.items()
    ]
    return min(matched_ious)


def _register_camera_anchor(
    group: _GroupState,
    frame: _FrameFeatures,
    camera_indexes: dict[str, _BKTree],
) -> None:
    camera_id = frame.source.camera_id
    if camera_id in group.camera_anchors:
        return
    group.camera_anchors[camera_id] = frame
    camera_indexes.setdefault(camera_id, _BKTree()).insert(
        frame.phash, _Anchor(group=group, frame=frame)
    )


def duplicate_group_id(policy_version: str, frame_ids: list[str]) -> str:
    value = f"{policy_version}:{':'.join(sorted(frame_ids))}".encode()
    return hashlib.sha256(value).hexdigest()[:24]


def _group_report(group_id: str, group: _GroupState) -> dict[str, Any]:
    representative = group.representative
    duplicates = sorted(
        group.duplicates, key=lambda relation: relation.duplicate.source.frame_id
    )
    return {
        "schema_version": 1,
        "group_id": group_id,
        "representative": _frame_report(representative),
        "representative_selection_reason": _selection_reason(
            representative, [relation.duplicate for relation in duplicates]
        ),
        "duplicates": [
            {
                **_frame_report(relation.duplicate),
                "matched_against_frame_id": relation.matched_against.source.frame_id,
                "match_type": relation.match_type,
                "phash_hamming_distance": relation.phash_hamming_distance,
                "pixel_mae": relation.pixel_mae,
                "bbox_min_iou": relation.bbox_min_iou,
            }
            for relation in duplicates
        ],
    }


def _selection_reason(
    representative: _FrameFeatures, duplicates: list[_FrameFeatures]
) -> str:
    if representative.source.approval_type == "human-reviewed" and any(
        frame.source.approval_type != "human-reviewed" for frame in duplicates
    ):
        return "human-reviewed"
    if any(representative.sharpness > frame.sharpness for frame in duplicates):
        return "highest-sharpness"
    return "lowest-frame-id"


def _frame_report(frame: _FrameFeatures) -> dict[str, object]:
    return {
        "frame_id": frame.source.frame_id,
        "camera_id": frame.source.camera_id,
        "session_id": frame.source.session_id,
        "approval_type": frame.source.approval_type,
        "image_sha256": frame.source.image_sha256,
        "phash": f"{frame.phash:016x}",
        "sharpness": frame.sharpness,
    }


def _hamming_distance(first: int, second: int) -> int:
    return (first ^ second).bit_count()
