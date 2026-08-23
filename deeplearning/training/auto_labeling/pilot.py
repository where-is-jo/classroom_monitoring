from __future__ import annotations

import shutil
import tempfile
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .core import (
    SAFE_ID_PATTERN,
    frame_id_from_record,
    read_json,
    read_jsonl,
    sha256_file,
    utc_now_iso,
    verified_frame_image_path,
    write_json,
    write_jsonl,
)
from .errors import AutoLabelingError
from .quality import FrameQualityThresholds, inspect_frame_quality


@dataclass(frozen=True)
class PilotSessionPlan:
    split: str
    target_frames: int

    def __post_init__(self) -> None:
        if self.split not in {"train", "val"}:
            raise ValueError("pilot split은 train 또는 val이어야 합니다.")
        if self.target_frames < 1:
            raise ValueError("pilot target_frames는 1 이상이어야 합니다.")


def prepare_clean_pilot_run(
    source_run_dirs: Sequence[Path],
    output_root: Path,
    *,
    run_id: str,
    session_plan: Mapping[str, PilotSessionPlan],
    quality_thresholds: FrameQualityThresholds | None = None,
    excluded_frame_ids: Sequence[str] = (),
) -> Path:
    """기존 추출 run에서 정상 프레임을 세션·클립별로 균등 선별한다."""

    if not SAFE_ID_PATTERN.fullmatch(run_id):
        raise AutoLabelingError("pilot run_id 형식이 올바르지 않습니다.")
    if not source_run_dirs or not session_plan:
        raise AutoLabelingError("pilot 원본 run과 세션 계획이 필요합니다.")
    excluded_ids = [str(frame_id).strip() for frame_id in excluded_frame_ids]
    if any(not frame_id for frame_id in excluded_ids):
        raise AutoLabelingError("pilot 수동 제외 frame_id는 비어 있을 수 없습니다.")
    if len(excluded_ids) != len(set(excluded_ids)):
        raise AutoLabelingError("pilot 수동 제외 frame_id가 중복됐습니다.")
    excluded_id_set = set(excluded_ids)

    active_quality = quality_thresholds or FrameQualityThresholds()
    frames_by_session: dict[str, list[tuple[Path, dict[str, Any]]]] = defaultdict(list)
    sources_by_id: dict[str, dict[str, Any]] = {}
    source_runs: list[dict[str, object]] = []
    seen_frame_ids: set[str] = set()

    for raw_run_dir in source_run_dirs:
        source_root = raw_run_dir.resolve(strict=True)
        run_manifest = read_json(source_root / "run.json")
        if not isinstance(run_manifest, dict):
            raise AutoLabelingError("pilot 원본 run.json 형식이 올바르지 않습니다.")
        source_runs.append(
            {
                "run_id": run_manifest.get("run_id"),
                "run_sha256": sha256_file(source_root / "run.json"),
                "frames_sha256": sha256_file(source_root / "frames.jsonl"),
            }
        )
        raw_sources = run_manifest.get("sources")
        if not isinstance(raw_sources, list):
            raise AutoLabelingError("pilot 원본 run의 sources가 올바르지 않습니다.")
        for source in raw_sources:
            if not isinstance(source, dict) or not isinstance(
                source.get("source_id"), str
            ):
                raise AutoLabelingError("pilot 원본 source 항목이 올바르지 않습니다.")
            source_id = str(source["source_id"])
            if source_id in sources_by_id:
                raise AutoLabelingError("pilot 원본 source_id가 중복됐습니다.")
            sources_by_id[source_id] = source

        for frame in read_jsonl(source_root / "frames.jsonl"):
            frame_id = frame_id_from_record(frame)
            if frame_id in seen_frame_ids:
                raise AutoLabelingError("pilot 원본 frame_id가 중복됐습니다.")
            seen_frame_ids.add(frame_id)
            session_id = str(frame.get("session_id", ""))
            if session_id in session_plan:
                frames_by_session[session_id].append((source_root, frame))

    if set(frames_by_session) != set(session_plan):
        missing = sorted(set(session_plan) - set(frames_by_session))
        raise AutoLabelingError(f"pilot 원본에 계획한 세션이 없습니다: {missing}")

    selected: list[tuple[Path, dict[str, Any], str]] = []
    quality_failures: list[dict[str, object]] = []
    manual_exclusions: list[dict[str, object]] = []
    found_excluded_ids: set[str] = set()
    session_summaries: dict[str, dict[str, object]] = {}
    for session_id, plan in session_plan.items():
        eligible_by_source: dict[str, list[tuple[Path, dict[str, Any]]]] = defaultdict(
            list
        )
        input_count = 0
        for source_root, frame in frames_by_session[session_id]:
            input_count += 1
            frame_id = frame_id_from_record(frame)
            if frame_id in excluded_id_set:
                found_excluded_ids.add(frame_id)
                manual_exclusions.append(
                    {
                        "frame_id": frame_id,
                        "session_id": session_id,
                        "source_id": frame.get("source_id"),
                        "timestamp_ms": frame.get("timestamp_ms"),
                        "reason": "operator-confirmed-recording-or-transmission-error",
                    }
                )
                continue
            image_path = verified_frame_image_path(source_root, frame)
            quality = inspect_frame_quality(image_path, active_quality)
            if quality["passed"] is not True:
                quality_failures.append(
                    {
                        "frame_id": frame_id,
                        "session_id": session_id,
                        "source_id": frame.get("source_id"),
                        "timestamp_ms": frame.get("timestamp_ms"),
                        "reasons": quality["reasons"],
                    }
                )
                continue
            eligible_by_source[str(frame.get("source_id", ""))].append(
                (source_root, frame)
            )

        eligible_count = sum(len(values) for values in eligible_by_source.values())
        if eligible_count < plan.target_frames:
            raise AutoLabelingError(
                f"session_id={session_id}: 정상 프레임 {eligible_count}장은 "
                f"목표 {plan.target_frames}장보다 적습니다."
            )
        allocations = _allocate_frames(eligible_by_source, plan.target_frames)
        session_selected: list[tuple[Path, dict[str, Any], str]] = []
        for source_id in sorted(eligible_by_source):
            ordered = sorted(
                eligible_by_source[source_id],
                key=lambda item: int(item[1].get("timestamp_ms", 0)),
            )
            for source_root, frame in _evenly_spaced(ordered, allocations[source_id]):
                session_selected.append((source_root, frame, plan.split))
        selected.extend(session_selected)
        session_summaries[session_id] = {
            "split": plan.split,
            "input_frame_count": input_count,
            "quality_passed_frame_count": eligible_count,
            "manually_excluded_frame_count": sum(
                item["session_id"] == session_id for item in manual_exclusions
            ),
            "selected_frame_count": len(session_selected),
            "source_allocations": allocations,
        }

    missing_exclusions = sorted(excluded_id_set - found_excluded_ids)
    if missing_exclusions:
        raise AutoLabelingError(
            f"pilot 원본의 계획 세션에서 수동 제외 frame_id를 찾지 못했습니다: "
            f"{missing_exclusions}"
        )

    target = (output_root / run_id).resolve()
    if target.exists():
        raise AutoLabelingError("같은 pilot run_id의 출력이 이미 있습니다.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{run_id}-", dir=target.parent) as temp:
        temporary = Path(temp)
        frames_dir = temporary / "frames"
        frames_dir.mkdir()
        output_frames: list[dict[str, Any]] = []
        selected_source_counts: dict[str, int] = defaultdict(int)
        for source_root, raw_frame, split in sorted(
            selected,
            key=lambda item: (
                str(item[1].get("session_id", "")),
                str(item[1].get("source_id", "")),
                int(item[1].get("timestamp_ms", 0)),
            ),
        ):
            frame = dict(raw_frame)
            frame_id = frame_id_from_record(frame)
            source_image = verified_frame_image_path(source_root, frame)
            target_image = frames_dir / f"{frame_id}.jpg"
            shutil.copy2(source_image, target_image)
            frame["requested_split"] = split
            frame["image_path"] = f"frames/{frame_id}.jpg"
            frame["image_sha256"] = sha256_file(target_image)
            output_frames.append(frame)
            selected_source_counts[str(frame.get("source_id", ""))] += 1

        selected_sources: list[dict[str, Any]] = []
        for source_id, frame_count in sorted(selected_source_counts.items()):
            source = dict(sources_by_id[source_id])
            session_id = str(source.get("session_id", ""))
            source["requested_split"] = session_plan[session_id].split
            source["frame_count"] = frame_count
            selected_sources.append(source)

        write_jsonl(temporary / "frames.jsonl", output_frames)
        write_json(
            temporary / "quality-report.json",
            {
                "schema_version": 1,
                "status": "passed",
                "thresholds": asdict(active_quality),
                "selected_frame_count": len(output_frames),
                "quality_failed_frame_count": len(quality_failures),
                "quality_failures": quality_failures,
                "manually_excluded_frame_count": len(manual_exclusions),
                "manual_exclusions": sorted(
                    manual_exclusions,
                    key=lambda item: str(item["frame_id"]),
                ),
                "sessions": session_summaries,
                "created_at": utc_now_iso(),
            },
        )
        write_json(
            temporary / "run.json",
            {
                "schema_version": 1,
                "run_id": run_id,
                "manifest_sha256": sha256_file(temporary / "quality-report.json"),
                "sampling_interval_seconds": None,
                "sampling_policy_version": "clean-pilot-stratified-v1",
                "jpeg_quality": 95,
                "approved_student_data": any(
                    source.get("subject_category") == "student"
                    for source in selected_sources
                ),
                "prepared_at": utc_now_iso(),
                "frame_count": len(output_frames),
                "sources": selected_sources,
                "source_runs": source_runs,
                "pilot_session_plan": {
                    session_id: asdict(plan)
                    for session_id, plan in session_plan.items()
                },
            },
        )
        _promote_temporary_directory(temporary, target)
    return target


def replace_pilot_frame(
    pilot_run_dir: Path,
    source_run_dirs: Sequence[Path],
    output_root: Path,
    *,
    run_id: str,
    excluded_frame_id: str,
    replacement_frame_id: str,
    quality_thresholds: FrameQualityThresholds | None = None,
) -> Path:
    """검수 중 발견된 불량 frame 하나만 새 정상 frame으로 교체한다."""

    if not SAFE_ID_PATTERN.fullmatch(run_id):
        raise AutoLabelingError("replacement pilot run_id 형식이 올바르지 않습니다.")
    if excluded_frame_id == replacement_frame_id:
        raise AutoLabelingError("제외 frame과 교체 frame은 달라야 합니다.")
    parent = pilot_run_dir.resolve(strict=True)
    parent_manifest = read_json(parent / "run.json")
    if not isinstance(parent_manifest, dict):
        raise AutoLabelingError("replacement 원본 run.json이 올바르지 않습니다.")
    parent_frames = read_jsonl(parent / "frames.jsonl")
    frame_by_id = {frame_id_from_record(frame): frame for frame in parent_frames}
    if len(frame_by_id) != len(parent_frames):
        raise AutoLabelingError("replacement 원본 frame_id가 중복됐습니다.")
    excluded = frame_by_id.get(excluded_frame_id)
    if excluded is None:
        raise AutoLabelingError("제외할 frame_id가 pilot에 없습니다.")
    if replacement_frame_id in frame_by_id:
        raise AutoLabelingError("교체 frame_id가 이미 pilot에 있습니다.")

    source_by_id: dict[str, dict[str, Any]] = {}
    source_frame: tuple[Path, dict[str, Any]] | None = None
    for raw_source_run in source_run_dirs:
        source_root = raw_source_run.resolve(strict=True)
        source_manifest = read_json(source_root / "run.json")
        if not isinstance(source_manifest, dict) or not isinstance(
            source_manifest.get("sources"), list
        ):
            raise AutoLabelingError("replacement source run이 올바르지 않습니다.")
        for raw_source in source_manifest["sources"]:
            if not isinstance(raw_source, dict):
                raise AutoLabelingError("replacement source 항목이 올바르지 않습니다.")
            source_id = str(raw_source.get("source_id", ""))
            if source_id in source_by_id and source_by_id[source_id] != raw_source:
                raise AutoLabelingError("replacement source_id 정보가 충돌합니다.")
            source_by_id[source_id] = raw_source
        for frame in read_jsonl(source_root / "frames.jsonl"):
            if frame_id_from_record(frame) == replacement_frame_id:
                if source_frame is not None:
                    raise AutoLabelingError(
                        "replacement frame_id가 여러 run에 있습니다."
                    )
                source_frame = (source_root, frame)
    if source_frame is None:
        raise AutoLabelingError(
            "replacement frame_id를 source run에서 찾지 못했습니다."
        )

    replacement_root, raw_replacement = source_frame
    replacement = dict(raw_replacement)
    split = str(excluded.get("requested_split", ""))
    if split not in {"train", "val"}:
        raise AutoLabelingError("제외 frame의 split이 올바르지 않습니다.")
    replacement_session = str(replacement.get("session_id", ""))
    session_splits = {
        str(frame.get("session_id", "")): str(frame.get("requested_split", ""))
        for frame in parent_frames
    }
    existing_replacement_split = session_splits.get(replacement_session)
    if existing_replacement_split is not None and existing_replacement_split != split:
        raise AutoLabelingError("replacement session이 다른 split에 이미 있습니다.")
    replacement["requested_split"] = split

    active_quality = quality_thresholds or FrameQualityThresholds()
    replacement_source_image = verified_frame_image_path(replacement_root, replacement)
    replacement_quality = inspect_frame_quality(
        replacement_source_image, active_quality
    )
    if replacement_quality["passed"] is not True:
        raise AutoLabelingError("replacement frame이 품질 검사를 통과하지 못했습니다.")

    output_frames = [
        dict(frame)
        for frame in parent_frames
        if frame_id_from_record(frame) != excluded_frame_id
    ]
    output_frames.append(replacement)
    target = (output_root / run_id).resolve()
    if target.exists():
        raise AutoLabelingError("같은 replacement pilot run_id 출력이 이미 있습니다.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{run_id}-", dir=target.parent) as temp:
        temporary = Path(temp)
        frames_dir = temporary / "frames"
        frames_dir.mkdir()
        copied_frames: list[dict[str, Any]] = []
        quality_failures: list[dict[str, object]] = []
        for raw_frame in sorted(
            output_frames,
            key=lambda item: (
                str(item.get("session_id", "")),
                str(item.get("source_id", "")),
                int(item.get("timestamp_ms", 0)),
            ),
        ):
            frame = dict(raw_frame)
            frame_id = frame_id_from_record(frame)
            if frame_id == replacement_frame_id:
                source_image = replacement_source_image
            else:
                source_image = verified_frame_image_path(parent, frame)
            quality = inspect_frame_quality(source_image, active_quality)
            if quality["passed"] is not True:
                quality_failures.append(
                    {
                        "frame_id": frame_id,
                        "session_id": frame.get("session_id"),
                        "reasons": quality["reasons"],
                    }
                )
                continue
            target_image = frames_dir / f"{frame_id}.jpg"
            shutil.copy2(source_image, target_image)
            frame["image_path"] = f"frames/{frame_id}.jpg"
            frame["image_sha256"] = sha256_file(target_image)
            copied_frames.append(frame)
        if quality_failures or len(copied_frames) != len(parent_frames):
            raise AutoLabelingError(
                f"replacement pilot 전체 품질 검사에 실패했습니다: {quality_failures}"
            )

        selected_source_counts: dict[str, int] = defaultdict(int)
        session_counts: dict[tuple[str, str], int] = defaultdict(int)
        for frame in copied_frames:
            selected_source_counts[str(frame.get("source_id", ""))] += 1
            session_counts[
                (
                    str(frame.get("session_id", "")),
                    str(frame.get("requested_split", "")),
                )
            ] += 1
        selected_sources: list[dict[str, Any]] = []
        for source_id, frame_count in sorted(selected_source_counts.items()):
            if source_id not in source_by_id:
                raise AutoLabelingError("replacement source metadata가 없습니다.")
            source = dict(source_by_id[source_id])
            source["frame_count"] = frame_count
            source["requested_split"] = next(
                str(frame.get("requested_split", ""))
                for frame in copied_frames
                if str(frame.get("source_id", "")) == source_id
            )
            selected_sources.append(source)

        replacement_record = {
            "excluded_frame_id": excluded_frame_id,
            "excluded_session_id": excluded.get("session_id"),
            "replacement_frame_id": replacement_frame_id,
            "replacement_session_id": replacement.get("session_id"),
            "split": split,
            "reason": "operator-confirmed-recording-or-transmission-error",
        }
        write_jsonl(temporary / "frames.jsonl", copied_frames)
        write_json(
            temporary / "quality-report.json",
            {
                "schema_version": 2,
                "status": "passed",
                "thresholds": asdict(active_quality),
                "selected_frame_count": len(copied_frames),
                "quality_failed_frame_count": 0,
                "quality_failures": [],
                "targeted_replacements": [replacement_record],
                "sessions": {
                    session_id: {"split": session_split, "selected_frame_count": count}
                    for (session_id, session_split), count in sorted(
                        session_counts.items()
                    )
                },
                "created_at": utc_now_iso(),
            },
        )
        write_json(
            temporary / "run.json",
            {
                "schema_version": 1,
                "run_id": run_id,
                "manifest_sha256": sha256_file(temporary / "quality-report.json"),
                "sampling_interval_seconds": None,
                "sampling_policy_version": "clean-pilot-targeted-replacement-v1",
                "jpeg_quality": parent_manifest.get("jpeg_quality", 95),
                "approved_student_data": parent_manifest.get(
                    "approved_student_data", False
                ),
                "prepared_at": utc_now_iso(),
                "frame_count": len(copied_frames),
                "sources": selected_sources,
                "source_runs": parent_manifest.get("source_runs", []),
                "parent_run": {
                    "run_id": parent_manifest.get("run_id"),
                    "run_sha256": sha256_file(parent / "run.json"),
                    "frames_sha256": sha256_file(parent / "frames.jsonl"),
                },
                "targeted_replacements": [replacement_record],
                "pilot_session_plan": {
                    session_id: {
                        "split": session_split,
                        "target_frames": count,
                    }
                    for (session_id, session_split), count in sorted(
                        session_counts.items()
                    )
                },
            },
        )
        _promote_temporary_directory(temporary, target)
    return target


def _promote_temporary_directory(temporary: Path, target: Path) -> None:
    try:
        temporary.replace(target)
        return
    except PermissionError as exc:
        # Windows에서는 같은 볼륨이어도 디렉터리 os.replace가 WinError 5로
        # 거부될 수 있다. 대상이 아직 없을 때만 검증 가능한 복사로 승격한다.
        if target.exists():
            raise AutoLabelingError("pilot 출력 폴더가 승격 중 생성됐습니다.") from exc

    try:
        shutil.copytree(temporary, target)
        _verify_copied_tree(temporary, target)
    except Exception as exc:
        if target.exists():
            shutil.rmtree(target)
        raise AutoLabelingError("pilot 출력 폴더 승격에 실패했습니다.") from exc


def _verify_copied_tree(source: Path, target: Path) -> None:
    source_files = {
        path.relative_to(source).as_posix(): sha256_file(path)
        for path in source.rglob("*")
        if path.is_file()
    }
    target_files = {
        path.relative_to(target).as_posix(): sha256_file(path)
        for path in target.rglob("*")
        if path.is_file()
    }
    if target_files != source_files:
        raise AutoLabelingError("pilot 복사 승격 결과의 파일 해시가 다릅니다.")


def _allocate_frames(
    groups: Mapping[str, list[tuple[Path, dict[str, Any]]]], target: int
) -> dict[str, int]:
    if not groups:
        raise AutoLabelingError("pilot에서 선택할 source가 없습니다.")
    allocations = {source_id: 0 for source_id in groups}
    ordered_ids = sorted(groups)
    remaining = target
    while remaining:
        progressed = False
        for source_id in ordered_ids:
            if allocations[source_id] >= len(groups[source_id]):
                continue
            allocations[source_id] += 1
            remaining -= 1
            progressed = True
            if remaining == 0:
                break
        if not progressed:
            raise AutoLabelingError("pilot source별 프레임 할당에 실패했습니다.")
    return allocations


def _evenly_spaced(
    values: list[tuple[Path, dict[str, Any]]], count: int
) -> list[tuple[Path, dict[str, Any]]]:
    if count < 1 or count > len(values):
        raise AutoLabelingError("pilot 균등 샘플 수가 올바르지 않습니다.")
    if count == 1:
        return [values[len(values) // 2]]
    indices = [round(index * (len(values) - 1) / (count - 1)) for index in range(count)]
    if len(set(indices)) != count:
        raise AutoLabelingError("pilot 균등 샘플 인덱스가 중복됐습니다.")
    return [values[index] for index in indices]
