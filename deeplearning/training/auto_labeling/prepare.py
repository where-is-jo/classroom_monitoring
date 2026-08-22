from __future__ import annotations

import tempfile
import time
from pathlib import Path

import cv2

from .core import (
    FrameRecord,
    Settings,
    SourceInput,
    frame_id_from_record,
    load_input_manifest,
    read_json,
    read_jsonl,
    sha256_file,
    stable_frame_id,
    utc_now_iso,
    verified_frame_image_path,
    write_json,
    write_jsonl,
)
from .errors import AutoLabelingError

DIRECTORY_REPLACE_RETRY_DELAYS_SECONDS = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 15.0)


def default_runs_root() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "auto-labeling" / "runs"


def prepare_run(
    manifest_path: Path,
    settings: Settings,
    *,
    output_root: Path | None = None,
    allow_approved_student_data: bool = False,
) -> Path:
    manifest_path = manifest_path.resolve(strict=True)
    manifest = load_input_manifest(
        manifest_path,
        allow_approved_student_data=allow_approved_student_data,
    )
    manifest_sha256 = sha256_file(manifest_path)
    runs_root = (output_root or default_runs_root()).resolve()
    runs_root.mkdir(parents=True, exist_ok=True)
    run_dir = runs_root / manifest.run_id
    if run_dir.exists():
        _verify_existing_run(run_dir, manifest_sha256)
        return run_dir

    with tempfile.TemporaryDirectory(
        prefix=f".{manifest.run_id}-", dir=runs_root
    ) as temporary:
        temporary_dir = Path(temporary)
        frames_dir = temporary_dir / "frames"
        frames_dir.mkdir()
        frame_records: list[FrameRecord] = []
        sanitized_sources: list[dict[str, object]] = []
        for source in manifest.sources:
            source_sha256 = sha256_file(source.file_path)
            source_records = _extract_source(
                source, source_sha256, frames_dir, settings
            )
            frame_records.extend(source_records)
            sanitized_sources.append(
                {
                    "source_id": source.source_id,
                    "source_sha256": source_sha256,
                    "approval_reference": source.approval_reference,
                    "consent_scope": source.consent_scope,
                    "retention_expires_at": source.retention_expires_at,
                    "camera_id": source.camera_id,
                    "session_id": source.session_id,
                    "captured_at": source.captured_at,
                    "subject_category": source.subject_category,
                    "usage": source.usage,
                    "requested_split": source.requested_split,
                    "frame_count": len(source_records),
                }
            )
        if not frame_records:
            raise AutoLabelingError("추출된 프레임이 없습니다.")
        write_jsonl(
            temporary_dir / "frames.jsonl",
            (record.to_dict() for record in frame_records),
        )
        write_json(
            temporary_dir / "run.json",
            {
                "schema_version": 1,
                "run_id": manifest.run_id,
                "manifest_sha256": manifest_sha256,
                "sampling_interval_seconds": settings.sampling_interval_seconds,
                "jpeg_quality": settings.jpeg_quality,
                "sampling_policy_version": settings.sampling_policy_version,
                "approved_student_data": any(
                    source.subject_category == "student" for source in manifest.sources
                ),
                "prepared_at": utc_now_iso(),
                "frame_count": len(frame_records),
                "sources": sanitized_sources,
            },
        )
        _replace_directory_with_retry(temporary_dir, run_dir)
    return run_dir


def _replace_directory_with_retry(source: Path, target: Path) -> None:
    """Windows의 일시적인 파일 잠금이 풀릴 때까지 run 발행을 제한적으로 재시도한다."""

    for delay_seconds in (*DIRECTORY_REPLACE_RETRY_DELAYS_SECONDS, None):
        try:
            source.replace(target)
            return
        except PermissionError as exc:
            if target.exists():
                raise AutoLabelingError(
                    "같은 run_id의 출력 디렉터리가 동시에 생성됐습니다."
                ) from exc
            if delay_seconds is None:
                raise AutoLabelingError(
                    "Windows 파일 잠금 때문에 준비된 run 디렉터리를 발행하지 "
                    "못했습니다. 잠시 후 다시 실행하세요."
                ) from exc
            time.sleep(delay_seconds)


def _verify_existing_run(run_dir: Path, manifest_sha256: str) -> None:
    run_manifest_path = run_dir / "run.json"
    frames_manifest_path = run_dir / "frames.jsonl"
    if not run_manifest_path.is_file() or not frames_manifest_path.is_file():
        raise AutoLabelingError("같은 run_id의 불완전한 실행 디렉터리가 있습니다.")
    run_manifest = read_json(run_manifest_path)
    if (
        not isinstance(run_manifest, dict)
        or run_manifest.get("manifest_sha256") != manifest_sha256
    ):
        raise AutoLabelingError("같은 run_id가 다른 입력 manifest에 이미 사용됐습니다.")
    frames = read_jsonl(frames_manifest_path)
    expected_count = run_manifest.get("frame_count")
    expected_ids = {frame_id_from_record(frame) for frame in frames}
    actual_ids = {path.stem for path in (run_dir / "frames").glob("*.jpg")}
    if expected_count != len(frames) or actual_ids != expected_ids:
        raise AutoLabelingError("기존 실행의 프레임 수가 manifest와 다릅니다.")
    for frame in frames:
        verified_frame_image_path(run_dir, frame)


def _extract_source(
    source: SourceInput,
    source_sha256: str,
    frames_dir: Path,
    settings: Settings,
) -> list[FrameRecord]:
    capture = cv2.VideoCapture(str(source.file_path))
    try:
        if not capture.isOpened():
            raise AutoLabelingError(
                f"source_id={source.source_id}: 영상을 열 수 없습니다."
            )
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        if fps <= 0:
            raise AutoLabelingError(
                f"source_id={source.source_id}: FPS를 확인할 수 없습니다."
            )
        interval_frames = max(1, round(fps * settings.sampling_interval_seconds))
        records: list[FrameRecord] = []
        frame_index = 0
        while True:
            read_ok, frame = capture.read()
            if not read_ok:
                break
            if frame_index % interval_frames == 0:
                timestamp_ms = round(frame_index * 1000 / fps)
                frame_id = stable_frame_id(
                    source_sha256, timestamp_ms, settings.sampling_policy_version
                )
                image_path = frames_dir / f"{frame_id}.jpg"
                if image_path.exists():
                    raise AutoLabelingError("서로 다른 프레임의 ID가 충돌했습니다.")
                write_ok = cv2.imwrite(
                    str(image_path),
                    frame,
                    [cv2.IMWRITE_JPEG_QUALITY, settings.jpeg_quality],
                )
                if not write_ok:
                    raise AutoLabelingError("프레임 JPEG를 저장할 수 없습니다.")
                records.append(
                    FrameRecord(
                        frame_id=frame_id,
                        source_id=source.source_id,
                        source_sha256=source_sha256,
                        timestamp_ms=timestamp_ms,
                        camera_id=source.camera_id,
                        session_id=source.session_id,
                        captured_at=source.captured_at,
                        approval_reference=source.approval_reference,
                        consent_scope=source.consent_scope,
                        retention_expires_at=source.retention_expires_at,
                        subject_category=source.subject_category,
                        usage=source.usage,
                        requested_split=source.requested_split,
                        image_path=f"frames/{frame_id}.jpg",
                        image_sha256=sha256_file(image_path),
                    )
                )
            frame_index += 1
        if frame_index == 0:
            raise AutoLabelingError(
                f"source_id={source.source_id}: 영상 프레임이 없습니다."
            )
        return records
    finally:
        capture.release()
