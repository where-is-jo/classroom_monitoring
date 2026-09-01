from __future__ import annotations

import csv
import hashlib
import html
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import cv2

from .core import SAFE_ID_PATTERN, sha256_bytes, sha256_file, write_json, write_jsonl
from .errors import AutoLabelingError

LOCAL_TIMESTAMP = re.compile(r"^(?P<date>\d{8})_(?P<time>\d{6})$")
PREFIXED_TIMESTAMP = re.compile(
    r"^(?P<camera>[A-Za-z0-9][A-Za-z0-9._-]{0,127})_"
    r"(?P<date>\d{8})_(?P<time>\d{6})$"
)
UTC_TIMESTAMP = re.compile(r"^(?P<date>\d{8})T(?P<time>\d{6})Z$")


@dataclass(frozen=True)
class ProbeResult:
    duration_seconds: float
    creation_time: str | None
    method: str


@dataclass
class VideoRecord:
    source_id: str
    relative_path: str
    absolute_path: str
    camera_id: str
    captured_at: str
    ended_at: str
    duration_seconds: float
    sha256: str
    timestamp_source: str
    probe_method: str
    status: str = "accepted"
    warning: str = ""
    duplicate_of: str = ""
    session_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    camera_id: str
    started_at: str
    ended_at: str
    clip_count: int
    total_duration_seconds: float
    gap_seconds: tuple[float, ...]
    source_ids: tuple[str, ...]
    relative_paths: tuple[str, ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["gap_seconds"] = list(self.gap_seconds)
        value["source_ids"] = list(self.source_ids)
        value["relative_paths"] = list(self.relative_paths)
        value["warnings"] = list(self.warnings)
        return value


DurationProbe = Callable[[Path], ProbeResult]


def scan_video_folder(
    input_dir: Path,
    output_dir: Path,
    *,
    timezone_name: str = "Asia/Seoul",
    camera_id: str | None = None,
    camera_map_path: Path | None = None,
    metadata_path: Path | None = None,
    session_overrides_path: Path | None = None,
    expected_clip_seconds: float = 300.0,
    session_gap_seconds: float = 60.0,
    overlap_tolerance_seconds: float = 2.0,
    duration_probe: DurationProbe | None = None,
) -> Path:
    """MP4 폴더를 읽기 전용으로 조사하고 결정적인 세션 manifest를 만든다."""

    root = _resolve_input_directory(input_dir)
    target = output_dir.resolve()
    _validate_options(
        camera_id,
        expected_clip_seconds,
        session_gap_seconds,
        overlap_tolerance_seconds,
    )
    if target.exists():
        raise AutoLabelingError(
            "출력 디렉터리가 이미 있습니다. 기존 결과를 덮어쓰지 않습니다."
        )
    try:
        local_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise AutoLabelingError("알 수 없는 timezone입니다.") from exc

    camera_map = _read_two_column_map(camera_map_path, "camera_id", required_value=True)
    metadata_map = _read_two_column_map(
        metadata_path, "captured_at", required_value=True
    )
    overrides = _read_two_column_map(
        session_overrides_path, "manual_session_id", required_value=True
    )
    paths = _discover_mp4_files(root)
    if not paths:
        raise AutoLabelingError("입력 폴더에서 MP4 파일을 찾지 못했습니다.")

    errors: list[dict[str, str]] = []
    records: list[VideoRecord] = []
    probe = duration_probe or probe_video
    hash_owner: dict[str, VideoRecord] = {}
    for path in paths:
        relative_path = path.relative_to(root).as_posix()
        try:
            resolved_camera = _resolve_camera_id(
                path,
                root,
                relative_path,
                camera_id=camera_id,
                camera_map=camera_map,
            )
            result = probe(path)
            if (
                not math.isfinite(result.duration_seconds)
                or result.duration_seconds <= 0
            ):
                raise AutoLabelingError("영상 길이가 0보다 큰 유한수가 아닙니다.")
            captured_at, timestamp_source = _resolve_captured_at(
                path,
                relative_path,
                local_timezone,
                metadata_map,
                result.creation_time,
            )
            digest = sha256_file(path)
            source_id = _stable_source_id(resolved_camera, captured_at, digest)
            warning = _duration_warning(result.duration_seconds, expected_clip_seconds)
            record = VideoRecord(
                source_id=source_id,
                relative_path=relative_path,
                absolute_path=str(path),
                camera_id=resolved_camera,
                captured_at=captured_at.isoformat(),
                ended_at=(
                    captured_at + timedelta(seconds=result.duration_seconds)
                ).isoformat(),
                duration_seconds=round(result.duration_seconds, 3),
                sha256=digest,
                timestamp_source=timestamp_source,
                probe_method=result.method,
                warning=warning,
            )
            duplicate = hash_owner.get(digest)
            if duplicate is not None:
                record.status = "duplicate"
                record.duplicate_of = duplicate.relative_path
                errors.append(
                    _error_row(
                        relative_path,
                        "duplicate",
                        f"동일 SHA-256 영상: {duplicate.relative_path}",
                    )
                )
            else:
                hash_owner[digest] = record
            records.append(record)
        except (AutoLabelingError, OSError, ValueError) as exc:
            errors.append(_error_row(relative_path, "invalid-video", str(exc)))

    accepted = [record for record in records if record.status == "accepted"]
    if not accepted:
        detail = errors[0]["message"] if errors else "처리 가능한 영상이 없습니다."
        raise AutoLabelingError(f"세션으로 만들 수 있는 영상이 없습니다: {detail}")
    _validate_mapping_paths(camera_map, paths, root, "camera_map.csv")
    _validate_mapping_paths(metadata_map, paths, root, "video_metadata.csv")
    _validate_mapping_paths(overrides, paths, root, "session_overrides.csv")

    sessions = _build_sessions(
        accepted,
        overrides,
        session_gap_seconds=session_gap_seconds,
        overlap_tolerance_seconds=overlap_tolerance_seconds,
        errors=errors,
    )
    if not sessions:
        raise AutoLabelingError("시간 겹침 오류로 생성할 수 있는 세션이 없습니다.")

    scan_config = {
        "schema_version": 1,
        "input_root": str(root),
        "timezone": timezone_name,
        "camera_id": camera_id,
        "expected_clip_seconds": expected_clip_seconds,
        "session_gap_seconds": session_gap_seconds,
        "overlap_tolerance_seconds": overlap_tolerance_seconds,
        "video_count": len(paths),
        "accepted_video_count": sum(record.status == "accepted" for record in records),
        "session_count": len(sessions),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{target.name}-", dir=target.parent
    ) as temp:
        temporary = Path(temp)
        write_jsonl(
            temporary / "video_inventory.jsonl", (item.to_dict() for item in records)
        )
        write_json(
            temporary / "session_manifest.json",
            {
                **scan_config,
                "scan_fingerprint": _scan_fingerprint(records, scan_config),
                "sessions": [session.to_dict() for session in sessions],
            },
        )
        _write_session_report(temporary / "session_report.csv", sessions)
        _write_errors(temporary / "scan_errors.csv", errors)
        _write_assignments(temporary / "session_assignments.csv", sessions)
        _write_timeline(temporary / "session_timeline.html", sessions, scan_config)
        temporary.replace(target)
    return target


def probe_video(path: Path) -> ProbeResult:
    """ffprobe를 우선 사용하고 없을 때 OpenCV로 길이와 재생 가능성을 확인한다."""

    executable = shutil.which("ffprobe")
    if executable:
        command = [
            executable,
            "-v",
            "error",
            "-show_entries",
            "format=duration:format_tags=creation_time",
            "-of",
            "json",
            str(path),
        ]
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if completed.returncode == 0:
            try:
                payload = json.loads(completed.stdout)
                format_data = payload.get("format", {})
                duration = float(format_data.get("duration"))
                creation_time = format_data.get("tags", {}).get("creation_time")
                if math.isfinite(duration) and duration > 0:
                    return ProbeResult(duration, creation_time, "ffprobe")
            except (TypeError, ValueError, json.JSONDecodeError):
                pass

    capture = cv2.VideoCapture(str(path))
    try:
        if not capture.isOpened():
            raise AutoLabelingError("영상을 열 수 없습니다.")
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        frame_count = float(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        read_ok, _ = capture.read()
        if fps <= 0 or frame_count <= 0 or not read_ok:
            raise AutoLabelingError(
                "영상 FPS·프레임 또는 첫 프레임을 확인할 수 없습니다."
            )
        return ProbeResult(frame_count / fps, None, "opencv")
    finally:
        capture.release()


def _resolve_input_directory(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise AutoLabelingError("입력 폴더를 찾을 수 없습니다.") from exc
    if not resolved.is_dir() or _is_link_or_junction(resolved):
        raise AutoLabelingError("입력은 링크가 아닌 실제 디렉터리여야 합니다.")
    return resolved


def _discover_mp4_files(root: Path) -> list[Path]:
    discovered: list[Path] = []
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        directories[:] = sorted(
            directory
            for directory in directories
            if not _is_link_or_junction(current_path / directory)
        )
        for name in sorted(files):
            candidate = current_path / name
            if candidate.suffix.lower() == ".mp4" and not _is_link_or_junction(
                candidate
            ):
                discovered.append(candidate.resolve(strict=True))
    return sorted(discovered, key=lambda item: item.relative_to(root).as_posix())


def _is_link_or_junction(path: Path) -> bool:
    is_junction = getattr(path, "is_junction", None)
    return path.is_symlink() or bool(is_junction and is_junction())


def _resolve_camera_id(
    path: Path,
    root: Path,
    relative_path: str,
    *,
    camera_id: str | None,
    camera_map: dict[str, str],
) -> str:
    mapped = camera_map.get(relative_path)
    if mapped:
        return _safe_id(mapped, "camera_id")
    prefixed = PREFIXED_TIMESTAMP.fullmatch(path.stem)
    if prefixed:
        return _safe_id(prefixed.group("camera"), "camera_id")
    if camera_id:
        return _safe_id(camera_id, "camera_id")
    relative = path.relative_to(root)
    if len(relative.parts) >= 2:
        return _safe_id(relative.parent.name, "카메라 폴더명")
    raise AutoLabelingError(
        "평면 폴더 영상에는 --camera-id 또는 camera_map.csv가 필요합니다."
    )


def _resolve_captured_at(
    path: Path,
    relative_path: str,
    timezone: ZoneInfo,
    metadata_map: dict[str, str],
    creation_time: str | None,
) -> tuple[datetime, str]:
    prefixed = PREFIXED_TIMESTAMP.fullmatch(path.stem)
    local = LOCAL_TIMESTAMP.fullmatch(path.stem)
    utc = UTC_TIMESTAMP.fullmatch(path.stem)
    if prefixed or local:
        match = prefixed or local
        assert match is not None
        parsed = datetime.strptime(
            f"{match.group('date')}{match.group('time')}", "%Y%m%d%H%M%S"
        ).replace(tzinfo=timezone)
        return parsed, "filename-local"
    if utc:
        parsed = datetime.strptime(
            f"{utc.group('date')}{utc.group('time')}", "%Y%m%d%H%M%S"
        ).replace(tzinfo=UTC)
        return parsed, "filename-utc"
    mapped = metadata_map.get(relative_path)
    if mapped:
        return _aware_datetime(mapped), "metadata-csv"
    if creation_time:
        return _aware_datetime(creation_time), "creation-time"
    raise AutoLabelingError(
        "촬영 시각을 판정할 수 없습니다. 파일명 또는 video_metadata.csv를 확인하세요."
    )


def _aware_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AutoLabelingError(
            "captured_at은 timezone 포함 ISO 8601이어야 합니다."
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AutoLabelingError("captured_at에는 timezone이 있어야 합니다.")
    return parsed


def _build_sessions(
    records: list[VideoRecord],
    overrides: dict[str, str],
    *,
    session_gap_seconds: float,
    overlap_tolerance_seconds: float,
    errors: list[dict[str, str]],
) -> list[SessionRecord]:
    by_camera: dict[str, list[VideoRecord]] = defaultdict(list)
    for record in records:
        by_camera[record.camera_id].append(record)

    automatic_groups: list[list[VideoRecord]] = []
    for camera in sorted(by_camera):
        current: list[VideoRecord] = []
        previous_end: datetime | None = None
        for record in sorted(
            by_camera[camera], key=lambda item: (item.captured_at, item.sha256)
        ):
            start = _aware_datetime(record.captured_at)
            end = _aware_datetime(record.ended_at)
            if end <= start:
                record.status = "invalid-time-range"
                errors.append(
                    _error_row(
                        record.relative_path,
                        record.status,
                        "종료 시각이 시작보다 빠릅니다.",
                    )
                )
                continue
            if previous_end is not None:
                gap = (start - previous_end).total_seconds()
                if gap < -overlap_tolerance_seconds:
                    record.status = "overlap-error"
                    errors.append(
                        _error_row(
                            record.relative_path,
                            record.status,
                            f"이전 영상과 {abs(gap):.3f}초 겹칩니다.",
                        )
                    )
                    continue
                if gap > session_gap_seconds:
                    automatic_groups.append(current)
                    current = []
            current.append(record)
            previous_end = end
        if current:
            automatic_groups.append(current)

    groups: list[tuple[str | None, list[VideoRecord]]] = []
    manual: dict[str, list[VideoRecord]] = defaultdict(list)
    for group in automatic_groups:
        remaining: list[VideoRecord] = []
        for record in group:
            manual_id = overrides.get(record.relative_path)
            if manual_id:
                manual[_safe_id(manual_id, "manual_session_id")].append(record)
            else:
                remaining.append(record)
        if remaining:
            groups.append((None, remaining))
    groups.extend((manual_id, manual[manual_id]) for manual_id in sorted(manual))

    sessions: list[SessionRecord] = []
    for manual_id, group in groups:
        group.sort(key=lambda item: (item.captured_at, item.sha256))
        cameras = {item.camera_id for item in group}
        if len(cameras) != 1:
            raise AutoLabelingError(
                "수동 세션은 서로 다른 카메라 영상을 합칠 수 없습니다."
            )
        gaps = [
            round(
                (
                    _aware_datetime(current.captured_at)
                    - _aware_datetime(previous.ended_at)
                ).total_seconds(),
                3,
            )
            for previous, current in zip(group, group[1:], strict=False)
        ]
        session_id = manual_id or _stable_session_id(group)
        for record in group:
            record.session_id = session_id
        warnings = tuple(item.warning for item in group if item.warning)
        sessions.append(
            SessionRecord(
                session_id=session_id,
                camera_id=group[0].camera_id,
                started_at=group[0].captured_at,
                ended_at=max(group, key=lambda item: item.ended_at).ended_at,
                clip_count=len(group),
                total_duration_seconds=round(
                    sum(item.duration_seconds for item in group), 3
                ),
                gap_seconds=tuple(gaps),
                source_ids=tuple(item.source_id for item in group),
                relative_paths=tuple(item.relative_path for item in group),
                warnings=warnings,
            )
        )
    return sorted(
        sessions, key=lambda item: (item.started_at, item.camera_id, item.session_id)
    )


def _stable_source_id(camera_id: str, captured_at: datetime, digest: str) -> str:
    timestamp = captured_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"source-{camera_id}-{timestamp}-{digest[:8]}"[:128]


def _stable_session_id(records: list[VideoRecord]) -> str:
    first = _aware_datetime(records[0].captured_at).astimezone(UTC)
    digest = sha256_bytes(":".join(record.sha256 for record in records).encode())[:8]
    value = f"session-{records[0].camera_id}-{first:%Y%m%dT%H%M%SZ}-{digest}"
    return value[:128]


def _duration_warning(duration: float, expected: float) -> str:
    lower = expected * 0.8
    upper = expected * 1.2
    if lower <= duration <= upper:
        return ""
    return f"예상 {expected:.0f}초와 다른 길이({duration:.3f}초)"


def _validate_options(
    camera_id: str | None,
    expected_clip_seconds: float,
    session_gap_seconds: float,
    overlap_tolerance_seconds: float,
) -> None:
    if camera_id:
        _safe_id(camera_id, "camera_id")
    for value, name in (
        (expected_clip_seconds, "expected_clip_seconds"),
        (session_gap_seconds, "session_gap_seconds"),
        (overlap_tolerance_seconds, "overlap_tolerance_seconds"),
    ):
        if not math.isfinite(value) or value < 0:
            raise AutoLabelingError(f"{name}은 0 이상의 유한수여야 합니다.")
    if expected_clip_seconds == 0:
        raise AutoLabelingError("expected_clip_seconds는 0보다 커야 합니다.")


def _safe_id(value: str, field_name: str) -> str:
    if SAFE_ID_PATTERN.fullmatch(value) is None:
        raise AutoLabelingError(f"{field_name} 형식이 올바르지 않습니다.")
    return value


def _read_two_column_map(
    path: Path | None, value_column: str, *, required_value: bool
) -> dict[str, str]:
    if path is None:
        return {}
    try:
        resolved = path.resolve(strict=True)
        with resolved.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"relative_path", value_column}
            if reader.fieldnames is None or not required.issubset(reader.fieldnames):
                raise AutoLabelingError(
                    f"{path.name}에는 relative_path,{value_column} 열이 필요합니다."
                )
            values: dict[str, str] = {}
            for row in reader:
                relative_path = (
                    str(row.get("relative_path", "")).replace("\\", "/").strip()
                )
                value = str(row.get(value_column, "")).strip()
                if not relative_path or (required_value and not value):
                    raise AutoLabelingError(f"{path.name}에 빈 필드가 있습니다.")
                if relative_path in values:
                    raise AutoLabelingError(
                        f"{path.name}에 중복 relative_path가 있습니다."
                    )
                values[relative_path] = value
            return values
    except OSError as exc:
        raise AutoLabelingError(f"{path.name}을 읽을 수 없습니다.") from exc


def _validate_mapping_paths(
    mapping: dict[str, str], paths: list[Path], root: Path, name: str
) -> None:
    known = {path.relative_to(root).as_posix() for path in paths}
    unknown = sorted(set(mapping) - known)
    if unknown:
        raise AutoLabelingError(
            f"{name}에 입력 폴더에 없는 경로가 있습니다: {unknown[0]}"
        )


def _scan_fingerprint(records: list[VideoRecord], config: dict[str, Any]) -> str:
    digest = hashlib.sha256()
    digest.update(json.dumps(config, sort_keys=True, separators=(",", ":")).encode())
    for record in sorted(records, key=lambda item: item.relative_path):
        digest.update(record.relative_path.encode())
        digest.update(record.sha256.encode())
        digest.update(record.session_id.encode())
    return digest.hexdigest()


def _error_row(relative_path: str, code: str, message: str) -> dict[str, str]:
    return {"relative_path": relative_path, "code": code, "message": message}


def _write_session_report(path: Path, sessions: list[SessionRecord]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "session_id",
                "camera_id",
                "started_at",
                "ended_at",
                "clip_count",
                "total_duration_seconds",
                "max_gap_seconds",
                "warnings",
            ],
        )
        writer.writeheader()
        for session in sessions:
            writer.writerow(
                {
                    "session_id": session.session_id,
                    "camera_id": session.camera_id,
                    "started_at": session.started_at,
                    "ended_at": session.ended_at,
                    "clip_count": session.clip_count,
                    "total_duration_seconds": session.total_duration_seconds,
                    "max_gap_seconds": max(session.gap_seconds, default=0.0),
                    "warnings": " | ".join(session.warnings),
                }
            )


def _write_errors(path: Path, errors: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["relative_path", "code", "message"])
        writer.writeheader()
        writer.writerows(errors)


def _write_assignments(path: Path, sessions: list[SessionRecord]) -> None:
    fields = [
        "session_id",
        "camera_id",
        "started_at",
        "ended_at",
        "clip_count",
        "role",
        "requested_split",
        "approval_reference",
        "consent_scope",
        "retention_expires_at",
        "subject_category",
        "evaluation_scope",
        "note",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for session in sessions:
            writer.writerow(
                {
                    "session_id": session.session_id,
                    "camera_id": session.camera_id,
                    "started_at": session.started_at,
                    "ended_at": session.ended_at,
                    "clip_count": session.clip_count,
                    "role": "",
                    "requested_split": "",
                    "approval_reference": "",
                    "consent_scope": "person-detection-training",
                    "retention_expires_at": "",
                    "subject_category": "",
                    "evaluation_scope": "",
                    "note": "",
                }
            )


def _write_timeline(
    path: Path, sessions: list[SessionRecord], config: dict[str, Any]
) -> None:
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(session.session_id)}</td>"
        f"<td>{html.escape(session.camera_id)}</td>"
        f"<td>{html.escape(session.started_at)}</td>"
        f"<td>{html.escape(session.ended_at)}</td>"
        f"<td>{session.clip_count}</td>"
        f"<td>{session.total_duration_seconds:.1f}</td>"
        f"<td>{html.escape(' | '.join(session.warnings))}</td>"
        "</tr>"
        for session in sessions
    )
    document = f"""<!doctype html>
<html lang=\"ko\"><head><meta charset=\"utf-8\"><title>영상 세션 타임라인</title>
<style>body{{font-family:Arial,sans-serif;margin:32px;color:#172033}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccd3df;padding:8px;text-align:left}}th{{background:#12233f;color:white}}tr:nth-child(even){{background:#f4f7fb}}code{{background:#eef2f7;padding:2px 5px}}</style></head>
<body><h1>5분 영상 세션 타임라인</h1>
<p>입력 영상 {config["video_count"]}개 · 채택 {config["accepted_video_count"]}개 · 세션 {config["session_count"]}개</p>
<p>같은 카메라에서 이전 영상 종료 후 <code>{config["session_gap_seconds"]}초</code> 이내 시작한 영상은 같은 세션입니다.</p>
<table><thead><tr><th>세션</th><th>카메라</th><th>시작</th><th>종료</th><th>클립</th><th>총 초</th><th>경고</th></tr></thead><tbody>{rows}</tbody></table>
</body></html>"""
    path.write_text(document, encoding="utf-8", newline="\n")
