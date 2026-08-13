"""Singleton repository and service assembly point."""

from __future__ import annotations

from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from fastapi import Depends
from pymongo import MongoClient

from ..classrooms.adapters.memory_repository import InMemoryClassroomRepository
from ..classrooms.adapters.mongo_repository import MongoClassroomRepository
from ..classrooms.ports import ClassroomRepository
from ..classrooms.service import ClassroomService
from ..demo_seed import seed_demo_data, seed_video_streams
from ..student_monitoring.adapters.memory_repository import (
    MemoryDetectionEventRepository,
    MemoryVideoSegmentRepository,
)
from ..student_monitoring.adapters.mongo_repository import (
    MongoDetectionEventRepository,
    MongoVideoSegmentRepository,
)
from ..student_monitoring.ports import DetectionEventRepository, VideoSegmentRepository
from ..student_monitoring.service import StudentMonitoringService
from ..video_monitoring.adapters.memory_repository import MemoryVideoStreamRepository
from ..video_monitoring.adapters.mongo_repository import MongoVideoStreamRepository
from ..video_monitoring.ports import VideoStreamRepository
from ..video_monitoring.service import VideoDemoService, VideoStreamService
from .broadcaster import InMemoryBroadcaster
from ..demo_seed import seed_demo_data
from ..face_enrollment.adapters.http_analyzer import HttpFaceAnalyzer
from ..face_enrollment.adapters.local_storage import LocalFaceObjectStorage
from ..face_enrollment.adapters.memory import (
    InMemoryFaceEnrollmentRepository,
    InMemoryFaceObjectStorage,
    SyntheticFaceAnalyzer,
)
from ..face_enrollment.models import PoseBin
from ..face_enrollment.rules import EnrollmentThresholds
from ..face_enrollment.service import FaceEnrollmentService
from ..snapshots.adapters.memory_storage import InMemorySnapshotStorage
from ..snapshots.ports import SnapshotStorage
from ..snapshots.service import SnapshotService
from ..video_monitoring.service import VideoDemoService
from .config import Settings
from .database import (
    DatabaseOperationError,
    MongoDatabase,
    MongoDocument,
    create_mongo_client,
    initialize_indexes,
    ping_database,
    select_database,
)
from .errors import DatabaseUnavailableError


def utc_now() -> datetime:
    return datetime.now(UTC)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


@lru_cache
def _classroom_repository() -> InMemoryClassroomRepository:
    return InMemoryClassroomRepository()


@lru_cache
def _detection_event_repository() -> MemoryDetectionEventRepository:
    return MemoryDetectionEventRepository()


@lru_cache
def _video_segment_repository() -> MemoryVideoSegmentRepository:
    return MemoryVideoSegmentRepository()


@lru_cache
def _video_stream_repository() -> MemoryVideoStreamRepository:
    return MemoryVideoStreamRepository()


@lru_cache
def _broadcaster() -> InMemoryBroadcaster:
    return InMemoryBroadcaster()


@lru_cache
def _mongo_client() -> MongoClient[MongoDocument]:
    settings = get_settings()
    if settings.database_url is None:
        raise DatabaseOperationError("DATABASE_URL is required.")
    return create_mongo_client(
        settings.database_url,
        timeout_seconds=settings.database_connect_timeout_seconds,
    )


@lru_cache
def _mongo_database() -> MongoDatabase:
    settings = get_settings()
    if settings.database_name is None:
        raise DatabaseOperationError("DATABASE_NAME is required.")
    return select_database(_mongo_client(), settings.database_name)


@lru_cache
def _mongo_classroom_repository() -> MongoClassroomRepository:
    return MongoClassroomRepository(_mongo_database())


@lru_cache
def _mongo_detection_event_repository() -> MongoDetectionEventRepository:
    return MongoDetectionEventRepository(_mongo_database())


@lru_cache
def _mongo_video_segment_repository() -> MongoVideoSegmentRepository:
    return MongoVideoSegmentRepository(_mongo_database())


@lru_cache
def _mongo_video_stream_repository() -> MongoVideoStreamRepository:
    return MongoVideoStreamRepository(_mongo_database())


def get_classroom_repository(
    settings: Settings = Depends(get_settings),
) -> ClassroomRepository:
    if settings.database_mode == "memory":
        return _classroom_repository()
    return _mongo_classroom_repository()


def get_detection_event_repository(
    settings: Settings = Depends(get_settings),
) -> DetectionEventRepository:
    if settings.database_mode == "memory":
        return _detection_event_repository()
    return _mongo_detection_event_repository()


def get_video_segment_repository(
    settings: Settings = Depends(get_settings),
) -> VideoSegmentRepository:
    if settings.database_mode == "memory":
        return _video_segment_repository()
    return _mongo_video_segment_repository()


def get_video_stream_repository(
    settings: Settings = Depends(get_settings),
) -> VideoStreamRepository:
    if settings.database_mode == "memory":
        return _video_stream_repository()
    return _mongo_video_stream_repository()


def get_broadcaster() -> InMemoryBroadcaster:
    return _broadcaster()


def get_classroom_service(
    repository: ClassroomRepository = Depends(get_classroom_repository),
    settings: Settings = Depends(get_settings),
) -> ClassroomService:
    return ClassroomService(
        repository,
        occupancy_confidence_threshold=settings.seat_occupancy_confidence_threshold,
        clock=utc_now,
    )


@lru_cache
def _snapshot_storage() -> SnapshotStorage:
    """스냅샷 저장소를 한 번만 만든다. 조립은 여기 한 곳에서만 한다.

    MinIO SDK import를 함수 안에 둔 이유는 memory backend로 도는 환경(테스트·로컬)에
    패키지가 없어도 기동해야 하기 때문이다.
    """
    settings = get_settings()
    if settings.snapshot_storage_backend == "memory":
        return InMemorySnapshotStorage()

    # 검증이 세 값의 존재를 이미 보장한다.
    assert settings.snapshot_storage_endpoint is not None
    assert settings.snapshot_storage_access_key is not None
    assert settings.snapshot_storage_secret_key is not None

    from ..snapshots.adapters.minio_storage import (
        MinioSnapshotStorage,
        build_minio_client,
    )

    client = build_minio_client(
        endpoint=settings.snapshot_storage_endpoint,
        access_key=settings.snapshot_storage_access_key.get_secret_value(),
        secret_key=settings.snapshot_storage_secret_key.get_secret_value(),
        secure=settings.snapshot_storage_secure,
        timeout_seconds=settings.snapshot_storage_timeout_seconds,
    )
    return MinioSnapshotStorage(client, settings.snapshot_storage_bucket)


def get_snapshot_service(
    settings: Settings = Depends(get_settings),
) -> SnapshotService:
    return SnapshotService(_snapshot_storage(), page_size_max=settings.page_size_max)


def get_video_demo_service(
    settings: Settings = Depends(get_settings),
) -> VideoDemoService:
    if settings.demo_mode_enabled and settings.app_env in {"local", "dev"}:
        return VideoDemoService(clock=utc_now)
    return VideoDemoService(streams=(), clips=(), clock=utc_now)


@lru_cache
def _face_enrollment_repository() -> InMemoryFaceEnrollmentRepository:
    return InMemoryFaceEnrollmentRepository()


@lru_cache
def _memory_face_object_storage() -> InMemoryFaceObjectStorage:
    return InMemoryFaceObjectStorage()


@lru_cache
def _local_face_object_storage(storage_dir: str) -> LocalFaceObjectStorage:
    return LocalFaceObjectStorage(Path(storage_dir))


def get_face_object_storage(
    settings: Settings,
) -> InMemoryFaceObjectStorage | LocalFaceObjectStorage:
    if settings.face_local_sample_storage_enabled:
        return _local_face_object_storage(str(settings.face_local_sample_storage_dir))
    return _memory_face_object_storage()


@lru_cache
def _face_analyzer(pose_run_lengths: tuple[tuple[PoseBin, int], ...]) -> SyntheticFaceAnalyzer:
    return SyntheticFaceAnalyzer(pose_run_lengths)


@lru_cache
def _http_face_analyzer(base_url: str, timeout_seconds: float) -> HttpFaceAnalyzer:
    return HttpFaceAnalyzer(base_url, timeout_seconds)


def get_face_enrollment_service(
    settings: Settings = Depends(get_settings),
) -> FaceEnrollmentService:
    pose_quotas = {
        PoseBin.FRONT: settings.face_pose_front_quota,
        PoseBin.LEFT: settings.face_pose_left_quota,
        PoseBin.RIGHT: settings.face_pose_right_quota,
        PoseBin.UP: settings.face_pose_up_quota,
        PoseBin.DOWN: settings.face_pose_down_quota,
    }
    return FaceEnrollmentService(
        _face_enrollment_repository(),
        get_face_object_storage(settings),
        (
            _http_face_analyzer(settings.face_analyzer_url, settings.face_analyzer_timeout_seconds)
            if settings.face_analyzer_mode == "http"
            else _face_analyzer(tuple(pose_quotas.items()))
        ),
        required_sample_count=settings.face_enrollment_required_samples,
        pose_quotas=pose_quotas,
        thresholds=EnrollmentThresholds(
            detection_confidence_min=settings.face_detection_confidence_min,
            face_size_ratio_min=settings.face_size_ratio_min,
            roll_degrees_max=settings.face_roll_degrees_max,
            blur_score_min=settings.face_blur_score_min,
            brightness_score_min=settings.face_brightness_score_min,
            landmark_confidence_min=settings.face_landmark_confidence_min,
            occlusion_score_max=settings.face_occlusion_score_max,
            duplicate_score_max=settings.face_duplicate_score_max,
            motion_speed_dps_max=settings.face_motion_speed_dps_max,
            yaw_side_degrees=settings.face_yaw_side_degrees,
            pitch_side_degrees=settings.face_pitch_side_degrees,
        ),
    )

def get_video_stream_service(
    repository: VideoStreamRepository = Depends(get_video_stream_repository),
    settings: Settings = Depends(get_settings),
) -> VideoStreamService:
    return VideoStreamService(
        repository=repository,
        stale_seconds=settings.detection_event_stale_seconds,
        clock=utc_now,
    )


def get_student_monitoring_service(
    detection_repository: DetectionEventRepository = Depends(get_detection_event_repository),
    segment_repository: VideoSegmentRepository = Depends(get_video_segment_repository),
    stream_repository: VideoStreamRepository = Depends(get_video_stream_repository),
    broadcaster: InMemoryBroadcaster = Depends(get_broadcaster),
) -> StudentMonitoringService:
    return StudentMonitoringService(
        detection_repository=detection_repository,
        segment_repository=segment_repository,
        stream_repository=stream_repository,
        broadcaster=broadcaster,
    )


def initialize_data_store() -> None:
    settings = get_settings()
    if settings.database_mode == "mongodb":
        database = _mongo_database()
        ping_database(database)
        initialize_indexes(
            database,
            [
                MongoClassroomRepository.ensure_indexes,
                MongoDetectionEventRepository.ensure_indexes,
                MongoVideoSegmentRepository.ensure_indexes,
                MongoVideoStreamRepository.ensure_indexes,
            ],
        )
        return
    if settings.demo_mode_enabled:
        seed_demo_data(
            get_classroom_service(get_classroom_repository(settings), settings), now=utc_now()
        )
        seed_video_streams(_video_stream_repository(), now=utc_now())


def close_data_store() -> None:
    if _mongo_client.cache_info().currsize:
        _mongo_client().close()
    _mongo_classroom_repository.cache_clear()
    _mongo_detection_event_repository.cache_clear()
    _mongo_video_segment_repository.cache_clear()
    _mongo_video_stream_repository.cache_clear()
    _mongo_database.cache_clear()
    _mongo_client.cache_clear()
    _face_enrollment_repository.cache_clear()
    _memory_face_object_storage.cache_clear()
    _local_face_object_storage.cache_clear()
    _face_analyzer.cache_clear()
    _http_face_analyzer.cache_clear()


def verify_readiness(settings: Settings = Depends(get_settings)) -> None:
    if settings.database_mode == "memory":
        return
    try:
        ping_database(_mongo_database())
    except DatabaseOperationError:
        raise DatabaseUnavailableError() from None
