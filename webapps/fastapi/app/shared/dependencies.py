"""Singleton repository and service assembly point."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from functools import lru_cache, partial
from pathlib import Path

from fastapi import Depends
from pymongo import MongoClient

from ..classrooms.adapters.memory_repository import (
    InMemoryClassroomRepository,
    InMemorySeatAssignmentRepository,
    InMemorySeatMigrationRepository,
    InMemorySeatMutationUnitOfWork,
)
from ..classrooms.adapters.mongo_repository import (
    MongoClassroomRepository,
    MongoSeatAssignmentRepository,
    MongoSeatMigrationRepository,
    MongoSeatMutationUnitOfWork,
)
from ..classrooms.migration import SeatMigrationService
from ..classrooms.ports import (
    ClassroomRepository,
    SeatAssignmentRepository,
    SeatMigrationRepository,
    SeatMutationUnitOfWork,
)
from ..classrooms.service import ClassroomService
from ..demo_seed import seed_demo_data, seed_roi_test_data, seed_video_streams
from ..entry_identity_events.adapters.memory import (
    InMemoryEntryIdentityEventRepository,
)
from ..entry_identity_events.adapters.mongo import MongoEntryIdentityEventRepository
from ..entry_identity_events.ports import EntryIdentityEventRepository
from ..entry_identity_events.service import EntryIdentityEventService
from ..face_embeddings.adapters.http_analyzer import HttpFaceEmbeddingAnalyzer
from ..face_embeddings.adapters.local_dataset import LocalFaceDatasetReader
from ..face_embeddings.adapters.memory import InMemoryFaceEmbeddingRepository
from ..face_embeddings.adapters.mongo import MongoFaceEmbeddingRepository
from ..face_embeddings.ports import FaceEmbeddingRepository
from ..face_embeddings.service import FaceEmbeddingService
from ..face_enrollment.adapters.http_analyzer import HttpFaceAnalyzer
from ..face_enrollment.adapters.local_storage import LocalFaceObjectStorage
from ..face_enrollment.adapters.memory import (
    InMemoryFaceEnrollmentRepository,
    InMemoryFaceObjectStorage,
    SyntheticFaceAnalyzer,
)
from ..face_enrollment.adapters.mongo import MongoFaceEnrollmentRepository
from ..face_enrollment.models import PoseBin
from ..face_enrollment.ports import FaceEnrollmentRepository
from ..face_enrollment.rules import EnrollmentThresholds
from ..face_enrollment.service import FaceEnrollmentService
from ..identity_handover.adapters.memory import (
    InMemoryIdentityHandoverRouteRepository,
)
from ..identity_handover.adapters.mongo import MongoIdentityHandoverRouteRepository
from ..identity_handover.ports import IdentityHandoverRouteRepository
from ..identity_handover.service import IdentityHandoverRouteService
from ..llm_search.adapters.llama_planner import LlamaQueryPlanner
from ..llm_search.adapters.stub_planner import StubQueryPlanner
from ..llm_search.ports import QueryPlanner
from ..llm_search.service import LlmSearchService
from ..roi_connections.adapters.detection_events import DetectionEventSeatedSource
from ..roi_connections.adapters.ffmpeg_camera import (
    FfmpegCameraFrameGrabber,
    UnavailableCameraFrameGrabber,
    parse_camera_sources,
)
from ..roi_connections.adapters.memory import InMemoryRoiConnectionRepository
from ..roi_connections.adapters.mongo import MongoRoiConnectionRepository
from ..roi_connections.ports import (
    CameraFrameGrabber,
    RoiConnectionRepository,
    SeatedDetectionSource,
)
from ..roi_connections.service import RoiConnectionService
from ..snapshots.adapters.memory_storage import InMemorySnapshotStorage
from ..snapshots.ports import SnapshotStorage
from ..snapshots.service import SnapshotService
from ..student_monitoring.adapters.memory_repository import (
    MemoryDetectionEventRepository,
    MemoryStudentStateRepository,
    MemoryVideoSegmentRepository,
)
from ..student_monitoring.adapters.mongo_repository import (
    MongoDetectionEventRepository,
    MongoStudentStateRepository,
    MongoVideoSegmentRepository,
)
from ..student_monitoring.ports import (
    DetectionEventRepository,
    StudentStateRepository,
    VideoSegmentRepository,
)
from ..student_monitoring.service import StudentMonitoringService
from ..students.adapters.memory import InMemoryStudentRepository
from ..students.adapters.mongo import MongoStudentRepository
from ..students.models import Student
from ..students.ports import StudentRepository
from ..students.service import StudentService
from ..video_monitoring.adapters.memory_playback_session_repository import (
    MemoryPlaybackSessionRepository,
)
from ..video_monitoring.adapters.memory_repository import MemoryVideoStreamRepository
from ..video_monitoring.adapters.mongo_playback_session_repository import (
    MongoPlaybackSessionRepository,
)
from ..video_monitoring.adapters.mongo_repository import MongoVideoStreamRepository
from ..video_monitoring.adapters.whep_client import HttpWhepClient
from ..video_monitoring.ports import PlaybackSessionRepository, VideoStreamRepository, WhepClient
from ..video_monitoring.service import (
    PlaybackSessionService,
    VideoDemoService,
    VideoStreamService,
)
from .broadcaster import InMemoryBroadcaster
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
from .student_identity import StudentLookupPort

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(UTC)


@lru_cache
def get_settings() -> Settings:
    return Settings()


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
def _student_state_repository() -> MemoryStudentStateRepository:
    return MemoryStudentStateRepository()


@lru_cache
def _video_stream_repository() -> MemoryVideoStreamRepository:
    return MemoryVideoStreamRepository()


@lru_cache
def _entry_identity_event_repository() -> InMemoryEntryIdentityEventRepository:
    return InMemoryEntryIdentityEventRepository(clock=utc_now)


@lru_cache
def _playback_session_repository() -> MemoryPlaybackSessionRepository:
    return MemoryPlaybackSessionRepository()


@lru_cache
def _memory_student_repository() -> InMemoryStudentRepository:
    now = utc_now()
    return InMemoryStudentRepository(
        tuple(
            Student(
                id=f"roi-test-student-{index}",
                student_number=f"2026{index:03d}",
                name=f"테스트 학생 {index}",
                birth_date=datetime(2012, 1, index, tzinfo=UTC).date(),
                classroom_name="ROI 테스트반",
                phone=None,
                guardian_phone="010-0000-0000",
                face_enrollment_id=None,
                face_registered=False,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            for index in range(1, 7)
        )
    )


@lru_cache
def _seat_assignment_repository() -> InMemorySeatAssignmentRepository:
    # UoW와 같은 store·lock을 공유해 mutation이 조회에 그대로 보이게 한다.
    return InMemorySeatAssignmentRepository(store=_classroom_repository())


@lru_cache
def _memory_seat_mutation_uow() -> InMemorySeatMutationUnitOfWork:
    return InMemorySeatMutationUnitOfWork(_classroom_repository(), clock=utc_now)


@lru_cache
def _memory_seat_migration_repository() -> InMemorySeatMigrationRepository:
    return InMemorySeatMigrationRepository()


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
def _mongo_seat_mutation_uow() -> MongoSeatMutationUnitOfWork:
    # 생성 시 topology(replica set)를 검사한다. 미지원이면 startup fail한다.
    return MongoSeatMutationUnitOfWork(_mongo_classroom_repository(), _mongo_database())


@lru_cache
def _mongo_seat_assignment_repository() -> MongoSeatAssignmentRepository:
    # UoW와 같은 database(client)를 공유해 UoW가 쓴 seat_assignments
    # 컬렉션을 읽기 경로에 그대로 노출한다.
    return MongoSeatAssignmentRepository(_mongo_database())


@lru_cache
def _mongo_seat_migration_repository() -> MongoSeatMigrationRepository:
    return MongoSeatMigrationRepository(_mongo_database())


@lru_cache
def _mongo_detection_event_repository() -> MongoDetectionEventRepository:
    return MongoDetectionEventRepository(_mongo_database())


@lru_cache
def _mongo_video_segment_repository() -> MongoVideoSegmentRepository:
    return MongoVideoSegmentRepository(_mongo_database())


@lru_cache
def _mongo_student_state_repository() -> MongoStudentStateRepository:
    return MongoStudentStateRepository(_mongo_database())


@lru_cache
def _mongo_video_stream_repository() -> MongoVideoStreamRepository:
    return MongoVideoStreamRepository(_mongo_database())


@lru_cache
def _mongo_entry_identity_event_repository() -> MongoEntryIdentityEventRepository:
    return MongoEntryIdentityEventRepository(_mongo_database())


@lru_cache
def _mongo_playback_session_repository() -> MongoPlaybackSessionRepository:
    return MongoPlaybackSessionRepository(_mongo_database())


@lru_cache
def _mongo_student_repository() -> MongoStudentRepository:
    return MongoStudentRepository(_mongo_database())


@lru_cache
def _memory_face_embedding_repository() -> InMemoryFaceEmbeddingRepository:
    return InMemoryFaceEmbeddingRepository()


@lru_cache
def _mongo_face_embedding_repository() -> MongoFaceEmbeddingRepository:
    return MongoFaceEmbeddingRepository(_mongo_database())


def get_face_embedding_repository() -> FaceEmbeddingRepository:
    if get_settings().database_mode == "memory":
        return _memory_face_embedding_repository()
    return _mongo_face_embedding_repository()


@lru_cache
def _face_embedding_analyzer(base_url: str, timeout_seconds: float) -> HttpFaceEmbeddingAnalyzer:
    return HttpFaceEmbeddingAnalyzer(base_url, timeout_seconds)


@lru_cache
def _face_dataset_reader(root: str) -> LocalFaceDatasetReader:
    return LocalFaceDatasetReader(Path(root))


@lru_cache
def _memory_roi_connection_repository() -> InMemoryRoiConnectionRepository:
    return InMemoryRoiConnectionRepository()


@lru_cache
def _mongo_roi_connection_repository() -> MongoRoiConnectionRepository:
    return MongoRoiConnectionRepository(_mongo_database())


@lru_cache
def _memory_identity_handover_route_repository() -> InMemoryIdentityHandoverRouteRepository:
    return InMemoryIdentityHandoverRouteRepository()


@lru_cache
def _mongo_identity_handover_route_repository() -> MongoIdentityHandoverRouteRepository:
    return MongoIdentityHandoverRouteRepository(_mongo_database())


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


def get_student_state_repository(
    settings: Settings = Depends(get_settings),
) -> StudentStateRepository:
    if settings.database_mode == "memory":
        return _student_state_repository()
    return _mongo_student_state_repository()


def get_video_stream_repository(
    settings: Settings = Depends(get_settings),
) -> VideoStreamRepository:
    if settings.database_mode == "memory":
        return _video_stream_repository()
    return _mongo_video_stream_repository()


def get_entry_identity_event_repository(
    settings: Settings = Depends(get_settings),
) -> EntryIdentityEventRepository:
    if settings.database_mode == "memory":
        return _entry_identity_event_repository()
    return _mongo_entry_identity_event_repository()


def get_playback_session_repository(
    settings: Settings = Depends(get_settings),
) -> PlaybackSessionRepository:
    if settings.database_mode == "memory":
        return _playback_session_repository()
    return _mongo_playback_session_repository()


@lru_cache
def _whep_client() -> HttpWhepClient:
    settings = get_settings()
    return HttpWhepClient(timeout_seconds=settings.whep_timeout_seconds)


def get_whep_client() -> WhepClient:
    return _whep_client()


def get_broadcaster() -> InMemoryBroadcaster:
    return _broadcaster()


def get_student_repository(
    settings: Settings = Depends(get_settings),
) -> StudentRepository:
    if settings.database_mode == "memory":
        return _memory_student_repository()
    return _mongo_student_repository()


def get_student_lookup() -> StudentLookupPort:
    settings = get_settings()
    return get_student_repository(settings)


def get_roi_connection_repository() -> RoiConnectionRepository:
    settings = get_settings()
    if settings.database_mode == "memory":
        return _memory_roi_connection_repository()
    return _mongo_roi_connection_repository()


def get_identity_handover_route_repository() -> IdentityHandoverRouteRepository:
    settings = get_settings()
    if settings.database_mode == "memory":
        return _memory_identity_handover_route_repository()
    return _mongo_identity_handover_route_repository()


def get_student_service(
    repository: StudentRepository = Depends(get_student_repository),
) -> StudentService:
    return StudentService(repository, clock=utc_now)


def get_face_embedding_service() -> FaceEmbeddingService:
    settings = get_settings()
    return FaceEmbeddingService(
        get_face_embedding_repository(),
        _face_embedding_analyzer(
            settings.face_analyzer_url, settings.face_analyzer_timeout_seconds
        ),
        _face_dataset_reader(str(settings.face_local_sample_storage_dir)),
        get_student_service(get_student_repository(settings)),
        clock=utc_now,
    )


def get_seat_mutation_uow(
    settings: Settings = Depends(get_settings),
) -> SeatMutationUnitOfWork:
    """좌석-학생 지정 mutation UoW.

    memory는 InMemoryClassroomRepository와 같은 store·lock을 공유하고,
    mongodb는 같은 database·client를 공유하는 MongoSeatMutationUnitOfWork를
    돌려준다.
    """
    if settings.database_mode == "memory":
        return _memory_seat_mutation_uow()
    return _mongo_seat_mutation_uow()


def get_seat_assignment_repository(
    settings: Settings = Depends(get_settings),
) -> SeatAssignmentRepository:
    """좌석-학생 지정 저장소 (읽기 경로).

    memory는 UoW와 같은 store·lock을 공유하고, mongodb는 UoW가 쓰는
    seat_assignments 컬렉션을 읽는 저장소를 돌려줘 쓰기·읽기 경로를 맞춘다.
    """
    if settings.database_mode == "memory":
        return _seat_assignment_repository()
    return _mongo_seat_assignment_repository()


def get_seat_migration_repository(
    settings: Settings = Depends(get_settings),
) -> SeatMigrationRepository:
    """오프라인 migration snapshot·record·approval 저장소."""
    if settings.database_mode == "memory":
        return _memory_seat_migration_repository()
    return _mongo_seat_migration_repository()


def get_seat_migration_service(
    repository: ClassroomRepository = Depends(get_classroom_repository),
    assignment_repository: SeatAssignmentRepository = Depends(get_seat_assignment_repository),
    migration_repository: SeatMigrationRepository = Depends(get_seat_migration_repository),
    settings: Settings = Depends(get_settings),
) -> SeatMigrationService:
    """오프라인 migration 서비스.

    cutover 게이트는 설정 ``MIGRATION_ENCRYPTION_TARGET_APPROVED``를 따른다.
    승인된 암호화 target/KMS가 없으면 migration run(cutover)을 차단한다.
    """
    return SeatMigrationService(
        repository,
        assignment_repository=assignment_repository,
        migration_repository=migration_repository,
        cutover_ready=lambda: settings.migration_encryption_target_approved,
        clock=utc_now,
    )


def get_classroom_service(
    repository: ClassroomRepository = Depends(get_classroom_repository),
    student_lookup: StudentLookupPort = Depends(get_student_lookup),
    uow: SeatMutationUnitOfWork = Depends(get_seat_mutation_uow),
    assignment_repository: SeatAssignmentRepository = Depends(get_seat_assignment_repository),
    settings: Settings = Depends(get_settings),
) -> ClassroomService:
    return ClassroomService(
        repository,
        student_lookup=student_lookup,
        assignment_repository=assignment_repository,
        uow=uow,
        occupancy_confidence_threshold=settings.seat_occupancy_confidence_threshold,
        clock=utc_now,
    )


@lru_cache
def _roi_classroom_service() -> ClassroomService:
    settings = get_settings()
    service = ClassroomService(
        InMemoryClassroomRepository(),
        occupancy_confidence_threshold=settings.seat_occupancy_confidence_threshold,
        clock=utc_now,
    )
    seed_roi_test_data(service)
    return service


@lru_cache
def get_camera_frame_grabber() -> CameraFrameGrabber:
    """카메라 캡처 어댑터를 고른다.

    접속 정보가 없으면 대역을 끼워 캡처만 막는다. 앱 기동을 막지 않는 이유는 ROI
    화면의 나머지 기능과 다른 화면이 카메라 없이도 동작해야 하기 때문이다.
    """
    settings = get_settings()
    if settings.camera_rtsp_sources is None:
        return UnavailableCameraFrameGrabber()
    raw = settings.camera_rtsp_sources.get_secret_value().strip()
    if not raw:
        return UnavailableCameraFrameGrabber()
    return FfmpegCameraFrameGrabber(
        parse_camera_sources(raw),
        timeout_seconds=settings.camera_frame_capture_timeout_seconds,
    )


@lru_cache
def _seated_detection_source() -> SeatedDetectionSource:
    """탐지 기반 ROI 자리 찾기가 읽을 표본 원천을 고른다(결정 0041).

    memory mode에서도 같은 어댑터를 쓴다 — 저장소만 다르고 읽는 방식이 같기 때문이다.
    """
    settings = get_settings()
    repository = (
        _detection_event_repository()
        if settings.database_mode == "memory"
        else _mongo_detection_event_repository()
    )
    return DetectionEventSeatedSource(
        repository,
        max_events=settings.roi_detection_sample_max_events,
    )


@lru_cache
def _roi_connection_service() -> RoiConnectionService:
    settings = get_settings()
    return RoiConnectionService(
        get_classroom_service(
            get_classroom_repository(settings),
            student_lookup=get_student_lookup(),
            uow=get_seat_mutation_uow(settings),
            assignment_repository=get_seat_assignment_repository(settings),
            settings=settings,
        ),
        get_student_lookup(),
        get_roi_connection_repository(),
        get_video_stream_repository(settings),
        get_camera_frame_grabber(),
        _seated_detection_source(),
        max_upload_bytes=settings.roi_reference_image_max_bytes,
        page_size_max=settings.page_size_max,
        clock=utc_now,
    )


def get_roi_connection_service() -> RoiConnectionService:
    return _roi_connection_service()


@lru_cache
def _identity_handover_route_service() -> IdentityHandoverRouteService:
    return IdentityHandoverRouteService(
        get_identity_handover_route_repository(),
        get_roi_connection_service(),
        get_camera_frame_grabber(),
        max_image_bytes=get_settings().roi_reference_image_max_bytes,
        clock=utc_now,
    )


def get_identity_handover_route_service() -> IdentityHandoverRouteService:
    return _identity_handover_route_service()


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


@lru_cache
def _stub_query_planner() -> StubQueryPlanner:
    return StubQueryPlanner()


@lru_cache
def _llama_query_planner(base_url: str, timeout_seconds: float, model: str) -> LlamaQueryPlanner:
    return LlamaQueryPlanner(base_url, timeout_seconds, model)


def _query_planner(settings: Settings) -> QueryPlanner:
    if settings.llm_search_mode == "llama":
        return _llama_query_planner(
            settings.llm_search_url,
            settings.llm_search_timeout_seconds,
            settings.llm_search_model,
        )
    return _stub_query_planner()


def get_llm_search_service(
    detection_repository: DetectionEventRepository = Depends(get_detection_event_repository),
    stream_repository: VideoStreamRepository = Depends(get_video_stream_repository),
    classroom_repository: ClassroomRepository = Depends(get_classroom_repository),
    snapshot_service: SnapshotService = Depends(get_snapshot_service),
    settings: Settings = Depends(get_settings),
) -> LlmSearchService | None:
    """자연어 검색 조립. **비활성 환경에서는 `None`이다.**

    스냅샷만 포트가 아니라 서비스를 받는다. 객체 키 규칙의 해석과 저장소 장애 처리가
    그 안에 있어서, 포트를 직접 넘기면 키 규칙이 세 번째로 복사된다.

    비활성을 여기서 판정하는 이유는 **조립 지점이 mode를 아는 유일한 곳**이기
    때문이다(결정 0002). 라우터가 `Settings`를 직접 읽으면 설정 세부가 HTTP 계층으로
    새고, 서비스가 읽으면 "만들어졌는데 쓰면 안 되는 객체"가 생긴다.

    예외를 던지지 않고 `None`을 돌려주는 이유는 화면 때문이다. 의존성이 예외를 던지면
    화면도 오류 페이지가 되어, 정작 보여줘야 할 **"왜 못 쓰는지"를 안내할 자리가
    사라진다.** 호출자 둘(API·화면)이 각자 알맞게 처리한다.
    """
    if settings.llm_search_mode == "disabled":
        return None
    return LlmSearchService(
        _query_planner(settings),
        detection_repository,
        stream_repository,
        classroom_repository,
        snapshot_service,
        max_span_days=settings.llm_search_max_span_days,
        scan_limit=settings.llm_search_scan_limit,
        clock=utc_now,
    )


def get_video_demo_service(
    settings: Settings = Depends(get_settings),
) -> VideoDemoService:
    if settings.demo_mode_enabled and settings.app_env in {"local", "dev"}:
        return VideoDemoService(clock=utc_now)
    return VideoDemoService(streams=(), clips=(), clock=utc_now)


@lru_cache
def _memory_face_enrollment_repository() -> InMemoryFaceEnrollmentRepository:
    return InMemoryFaceEnrollmentRepository()


@lru_cache
def _mongo_face_enrollment_repository() -> MongoFaceEnrollmentRepository:
    return MongoFaceEnrollmentRepository(_mongo_database())


def get_face_enrollment_repository() -> FaceEnrollmentRepository:
    if get_settings().database_mode == "memory":
        return _memory_face_enrollment_repository()
    return _mongo_face_enrollment_repository()


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
        get_face_enrollment_repository(),
        get_face_object_storage(settings),
        (
            _http_face_analyzer(settings.face_analyzer_url, settings.face_analyzer_timeout_seconds)
            if settings.face_analyzer_mode == "http"
            else _face_analyzer(tuple(pose_quotas.items()))
        ),
        required_sample_count=settings.face_enrollment_required_samples,
        augmented_sample_count=settings.face_enrollment_augmented_samples,
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
            pitch_down_degrees=settings.face_pitch_down_degrees,
        ),
        clock=utc_now,
    )


def get_video_stream_service(
    repository: VideoStreamRepository = Depends(get_video_stream_repository),
    classroom_service: ClassroomService = Depends(get_classroom_service),
    settings: Settings = Depends(get_settings),
) -> VideoStreamService:
    return VideoStreamService(
        repository=repository,
        classroom_service=classroom_service,
        stale_seconds=settings.detection_event_stale_seconds,
        clock=utc_now,
    )


def get_entry_identity_event_service(
    repository: EntryIdentityEventRepository = Depends(get_entry_identity_event_repository),
    stream_repository: VideoStreamRepository = Depends(get_video_stream_repository),
    broadcaster: InMemoryBroadcaster = Depends(get_broadcaster),
    student_lookup: StudentLookupPort = Depends(get_student_lookup),
    settings: Settings = Depends(get_settings),
) -> EntryIdentityEventService:
    return EntryIdentityEventService(
        repository,
        stream_repository,
        broadcaster,
        student_lookup,
        retention_days=settings.entry_identity_event_retention_days,
        page_size_max=settings.page_size_max,
        clock=utc_now,
    )


def get_playback_session_service(
    session_repository: PlaybackSessionRepository = Depends(get_playback_session_repository),
    stream_repository: VideoStreamRepository = Depends(get_video_stream_repository),
    whep_client: WhepClient = Depends(get_whep_client),
    settings: Settings = Depends(get_settings),
) -> PlaybackSessionService:
    return PlaybackSessionService(
        session_repository=session_repository,
        stream_repository=stream_repository,
        whep_client=whep_client,
        whep_base_url=settings.whep_base_url,
        ttl_seconds=settings.playback_session_ttl_seconds,
        clock=utc_now,
    )


def get_student_monitoring_service(
    detection_repository: DetectionEventRepository = Depends(get_detection_event_repository),
    segment_repository: VideoSegmentRepository = Depends(get_video_segment_repository),
    stream_repository: VideoStreamRepository = Depends(get_video_stream_repository),
    state_repository: StudentStateRepository = Depends(get_student_state_repository),
    broadcaster: InMemoryBroadcaster = Depends(get_broadcaster),
    classroom_service: ClassroomService = Depends(get_classroom_service),
    roi_service: RoiConnectionService = Depends(get_roi_connection_service),
    student_lookup: StudentLookupPort = Depends(get_student_lookup),
    settings: Settings = Depends(get_settings),
) -> StudentMonitoringService:
    return StudentMonitoringService(
        detection_repository=detection_repository,
        segment_repository=segment_repository,
        stream_repository=stream_repository,
        state_repository=state_repository,
        broadcaster=broadcaster,
        classroom_service=classroom_service,
        roi_service=roi_service,
        occupancy_confidence_threshold=settings.seat_occupancy_confidence_threshold,
        occupancy_hold_seconds=settings.seat_occupancy_hold_seconds,
        identity_confidence_threshold=settings.student_identity_confidence_threshold,
        identity_hold_seconds=settings.student_identity_hold_seconds,
        absent_grace_seconds=settings.student_absent_grace_seconds,
        stale_seconds=settings.detection_event_stale_seconds,
        history_limit=settings.student_state_history_limit,
        clock=utc_now,
        student_lookup=student_lookup,
    )


def _build_classroom_service(settings: Settings) -> ClassroomService:
    """FastAPI 요청 경로 밖(startup 검증·demo seed)에서
    ``ClassroomService``를 직접 조립한다.

    ``get_classroom_service`` 등은 파라미터 기본값이 ``fastapi.Depends(...)``
    마커이므로, 일반 Python 함수 호출로 부르면서 해당 인자를 생략하면 FastAPI DI가
    해석해주지 않아 ``Depends`` 객체 자체가 그대로 주입된다. 이 helper는 memory
    모드 하위 의존성(uow·assignment_repository·student_lookup)을 모두 명시적으로
    구성해 넘겨 그 문제를 피한다.
    """
    return get_classroom_service(
        get_classroom_repository(settings),
        student_lookup=get_student_lookup(),
        uow=get_seat_mutation_uow(settings),
        assignment_repository=get_seat_assignment_repository(settings),
        settings=settings,
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
                MongoSeatMutationUnitOfWork.ensure_indexes,
                MongoSeatMigrationRepository.ensure_indexes,
                # 보존 기간이 설정값이라 partial로 넘긴다. TTL 인덱스는 기간이 바뀌면
                # 옵션도 바뀌므로 초기화 시점에 현재 설정을 반영해야 한다.
                partial(
                    MongoDetectionEventRepository.ensure_indexes,
                    retention_days=settings.detection_event_retention_days,
                ),
                MongoVideoSegmentRepository.ensure_indexes,
                MongoStudentStateRepository.ensure_indexes,
                MongoVideoStreamRepository.ensure_indexes,
                MongoEntryIdentityEventRepository.ensure_indexes,
                MongoPlaybackSessionRepository.ensure_indexes,
                MongoStudentRepository.ensure_indexes,
                MongoRoiConnectionRepository.ensure_indexes,
                MongoIdentityHandoverRouteRepository.ensure_indexes,
                MongoFaceEmbeddingRepository.ensure_indexes,
                MongoFaceEnrollmentRepository.ensure_indexes,
            ],
        )
        # UoW 생성 시 transaction topology(replica set)를 검사한다.
        # 미지원 환경이면 DatabaseOperationError로 startup fail한다.
        _mongo_seat_mutation_uow()
        video_service = get_video_stream_service(
            get_video_stream_repository(settings),
            _build_classroom_service(settings),
            settings,
        )
        for stream in video_service.list_invalid_classroom_references():
            logger.error(
                "camera_id=%s stream_id=%s classroom_id=%s 활성 강의실 참조가 유효하지 않습니다.",
                stream.camera_id,
                stream.id,
                stream.classroom_id,
            )
        return
    # settings.database_mode는 memory|mongodb 둘 뿐이고 위에서 mongodb는 이미
    # return했으므로, 여기 도달했다면 항상 memory 모드다. demo mode는 문서상
    # memory 모드 전용 로컬 실행 경로다.
    if settings.demo_mode_enabled:
        classroom_service = _build_classroom_service(settings)
        seed_demo_data(
            classroom_service,
            now=utc_now(),
        )
        seed_video_streams(
            get_video_stream_service(_video_stream_repository(), classroom_service, settings),
            now=utc_now(),
        )


def close_data_store() -> None:
    if _mongo_client.cache_info().currsize:
        _mongo_client().close()
    _mongo_classroom_repository.cache_clear()
    _mongo_seat_assignment_repository.cache_clear()
    _mongo_seat_migration_repository.cache_clear()
    _mongo_seat_mutation_uow.cache_clear()
    _mongo_detection_event_repository.cache_clear()
    _student_state_repository.cache_clear()
    _mongo_student_state_repository.cache_clear()
    _mongo_video_segment_repository.cache_clear()
    _mongo_video_stream_repository.cache_clear()
    _mongo_entry_identity_event_repository.cache_clear()
    _entry_identity_event_repository.cache_clear()
    _mongo_playback_session_repository.cache_clear()
    _mongo_student_repository.cache_clear()
    _mongo_face_embedding_repository.cache_clear()
    _mongo_roi_connection_repository.cache_clear()
    _mongo_identity_handover_route_repository.cache_clear()
    _mongo_database.cache_clear()
    _mongo_client.cache_clear()
    _memory_face_object_storage.cache_clear()
    _local_face_object_storage.cache_clear()
    _face_analyzer.cache_clear()
    _http_face_analyzer.cache_clear()
    _roi_connection_service.cache_clear()
    _identity_handover_route_service.cache_clear()
    _roi_classroom_service.cache_clear()
    _memory_roi_connection_repository.cache_clear()
    _memory_identity_handover_route_repository.cache_clear()
    _memory_student_repository.cache_clear()
    _memory_face_enrollment_repository.cache_clear()
    _mongo_face_enrollment_repository.cache_clear()
    _memory_face_embedding_repository.cache_clear()
    _face_embedding_analyzer.cache_clear()
    _face_dataset_reader.cache_clear()
    _stub_query_planner.cache_clear()
    _llama_query_planner.cache_clear()


def verify_readiness(settings: Settings = Depends(get_settings)) -> None:
    if settings.database_mode == "memory":
        return
    try:
        ping_database(_mongo_database())
    except DatabaseOperationError:
        raise DatabaseUnavailableError() from None
