"""stream + inference 조립 진입점.

worker 디렉터리에서 `python -m pipeline.main`으로 실행한다.
설정을 읽고 객체를 조립하는 코드는 여기 한 곳에만 둔다. 워커 안에서 서로를
직접 조립하면 나중에 추론을 별도 프로세스로 뗄 때 고칠 곳이 흩어진다.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
from types import FrameType

from inference.config import DEFAULT_DATA_DIR as INFERENCE_DATA_DIR
from inference.config import InferenceSettings
from inference.consumer import (
    EntryResultHandler,
    InferenceConsumer,
    ResultHandler,
    log_result,
)
from inference.dispatch import AsyncResultDispatcher
from inference.detection_trace import (
    PersonDetectionTraceHandler,
    PersonDetectionTraceRecorder,
)
from inference.entry_overlay import FastAPIEntryOverlayHandler
from inference.face_identity import (
    EntryFaceProcessor,
    FastAPIEntryIdentityEventHandler,
    HttpFaceIdentifier,
)
from inference.handler import FastAPIResultHandler
from inference.identity_handover import (
    HttpIdentityHandoverRouteProvider,
    IdentityHandoverResultHandler,
    IdentityHandoverRoute,
    RefreshingIdentityHandoverResultHandler,
)
from inference.model import Yolo8nDetector
from inference.model_contract import verify_person_model_contract
from inference.overlay import FastAPIOverlayHandler
from inference.processor import InferenceProcessor
from inference.snapshot import SnapshotResultHandler
from inference.tracking import ByteTrackConfig, ByteTrackResultHandler
from inference.types import EntryFaceObservationBatch, InferenceResult
from pydantic import ValidationError
from shared.config_errors import format_validation_error
from shared.frame_buffer import FrameBuffer
from shared.logging_setup import configure_logging, use_utf8_console
from shared.metrics import register_frame_buffer, start_metrics_server
from shared.object_storage import ObjectStorageError
from shared.object_storage.factory import build_object_storage
from shared.types import CapturedFrame
from stream.config import StreamSettings
from stream.errors import StreamWorkerError
from stream.main import build_publisher
from stream.worker import StreamWorker

from .config import PIPELINE_ENV_FILE, PipelineSettings
from .runner import PipelineRunner, ResultDispatcher

logger = logging.getLogger(__name__)


def build_snapshot_handler(settings: InferenceSettings) -> ResultHandler:
    """스냅샷 적재 핸들러를 만든다. 저장소를 준비하지 못하면 로그 전용으로 낮춘다.

    **저장소가 없다고 파이프라인을 죽이지 않는다.** 이 워커의 본업은 탐지 결과를
    FastAPI로 넘기는 것이고, 스냅샷은 결정 0028이 기본값을 꺼 둔 부가 기능이다.
    MinIO가 내려가 있다는 이유로 기동에 실패하면, 있어도 그만인 기능 하나가
    반드시 돌아야 하는 기능 전체를 멈추는 것이 된다. 실제로 그랬다 —
    `ensure_bucket()`이 던진 ObjectStorageError를 아무도 잡지 않아 컨테이너가
    재시작만 반복했다.

    **낮춘 상태는 이번 실행 내내 유지된다.** MinIO가 나중에 올라와도 스냅샷은
    다시 켜지지 않으므로 워커를 재시작해야 한다. 프레임마다 다시 시도하게 두면
    접속 timeout(5초)이 추론 소비자 스레드를 그만큼 붙잡아, 살아 있기는 하지만
    프레임을 계속 버리는 상태가 된다. 조용히 느려지는 것보다 꺼진 것이 낫다.
    """
    try:
        storage = build_object_storage(
            settings, local_fallback_dir=INFERENCE_DATA_DIR / "snapshots"
        )
    except ObjectStorageError as error:
        logger.warning(
            "객체 저장소를 준비하지 못해 탐지 스냅샷 적재를 끄고 계속한다: %s. "
            "탐지와 FastAPI 전송은 그대로 돈다. 스냅샷이 필요하면 저장소를 살린 뒤 "
            "워커를 재시작한다.",
            error,
        )
        return log_result

    logger.info(
        "탐지 스냅샷 적재를 켠다. 긴 변 %dpx, 품질 %d, 카메라당 최소 간격 %.0f초. "
        "영상 원본은 저장하지 않는다(결정 0011).",
        settings.snapshot_max_long_side_px,
        settings.snapshot_jpeg_quality,
        settings.snapshot_min_interval_seconds,
    )
    return SnapshotResultHandler(
        storage=storage,
        min_interval_seconds=settings.snapshot_min_interval_seconds,
        max_long_side_px=settings.snapshot_max_long_side_px,
        jpeg_quality=settings.snapshot_jpeg_quality,
    )


def _discard_entry_result(
    _captured: CapturedFrame,
    _batch: EntryFaceObservationBatch,
) -> None:
    """인계 route가 없을 때도 얼굴 분석 결과를 일반 탐지로 바꾸지 않는다."""


def _sequence_entry_handlers(
    first: EntryResultHandler, second: EntryResultHandler
) -> EntryResultHandler:
    """입구 결과를 두 곳에 순서대로 넘긴다.

    `FastAPIEntryIdentityEventHandler`는 inner를 먼저 부르고 전송하는 구조라, 통째로
    전송 스레드에 넘기면 신원 인계 coordinator까지 그 스레드로 따라간다. coordinator는
    프레임 순서에 기대는 상태를 들고 있어 소비자 스레드에 남아야 한다. 그래서 둘을
    갈라 붙인다 — 인계 관측은 여기서 즉시, 전송은 dispatcher가.
    """

    def handle(captured: CapturedFrame, batch: EntryFaceObservationBatch) -> None:
        first(captured, batch)
        second(captured, batch)

    return handle


def _tee_handlers(first: ResultHandler, second: ResultHandler) -> ResultHandler:
    """결과를 두 곳에 순서대로 넘긴다.

    오버레이와 저장은 주기도 실패 정책도 달라 각자의 전송 스레드를 가진다. 둘 다
    큐에 넣고 즉시 돌아오므로 이 함수가 소비자 스레드를 붙잡지 않는다.
    """

    def handle(captured: CapturedFrame, result: InferenceResult) -> None:
        first(captured, result)
        second(captured, result)

    return handle


def build_result_handlers(
    settings: InferenceSettings,
    *,
    person_model_contract: dict[str, object] | None = None,
    fastapi_url: str | None = None,
    face_identity_url: str | None = None,
    person_tracking_config: ByteTrackConfig | None = None,
    person_tracking_camera_ids: frozenset[str] | None = None,
    identity_handover_routes: tuple[IdentityHandoverRoute, ...] = (),
    identity_handover_config_url: str | None = None,
    identity_handover_config_refresh_seconds: float = 5.0,
    identity_handover_config_timeout_seconds: float = 2.0,
    identity_handover_max_delay_seconds: float = 8.0,
    identity_handover_clock_skew_seconds: float = 0.5,
    identity_handover_track_stale_seconds: float = 30.0,
    identity_handover_min_confidence: float = 0.0,
    identity_track_recovery_enabled: bool = False,
    available_camera_ids: frozenset[str] | None = None,
    entry_camera_ids: frozenset[str] = frozenset(),
    classroom_camera_ids: frozenset[str] = frozenset(),
    result_dispatch_enabled: bool = False,
    result_dispatch_queue_maxsize: int = 32,
    result_dispatch_min_interval_seconds: float = 0.0,
    result_dispatch_close_timeout_seconds: float = 5.0,
    overlay_dispatch_enabled: bool = False,
    overlay_dispatch_min_interval_seconds: float = 0.0,
    overlay_timeout_seconds: float = 2.0,
) -> tuple[ResultHandler, EntryResultHandler | None, tuple[ResultDispatcher, ...]]:
    """CCTV 탐지와 입구 얼굴 관측의 서로 다른 결과 경계를 조립한다.

    스냅샷이 꺼져 있으면 저장소를 만들지 않는다. MinIO 접속 정보 없이도 파이프라인이
    돌아야 하고, 저장은 명시적으로 켜는 것이라는 규칙(결정 0011)에 맞춘다.
    켜져 있는데 저장소가 없는 경우는 `build_snapshot_handler`가 처리한다 —
    기동에 실패하지 않고 스냅샷만 끈다.

    `fastapi_url`이 주어지면(FASTAPI_URL을 설정한 경우) HTTP 전송 핸들러로 감싼다.
    감싼 핸들러는 기존 로그·스냅샷 동작을 먼저 수행하고 그다음 전송하므로, 전송이
    실패해도 탐지 기록은 남는다.
    """
    handler: ResultHandler = (
        build_snapshot_handler(settings) if settings.snapshot_enabled else log_result
    )
    dispatchers: list[ResultDispatcher] = []

    if fastapi_url is not None:
        logger.info("탐지 결과를 FastAPI(%s)로 전송한다.", fastapi_url)
        handler = FastAPIResultHandler(fastapi_url, inner=handler)
        if result_dispatch_enabled:
            # **여기가 소비자 스레드와 네트워크의 경계다.** 아래(전송·스냅샷)는 전용
            # 스레드가, 위(ByteTrack·신원 인계)는 소비자 스레드가 맡는다.
            logger.info(
                "탐지 결과 전송을 별도 스레드로 분리한다. 큐 %d건, 카메라당 최소 간격 %.2f초.",
                result_dispatch_queue_maxsize,
                result_dispatch_min_interval_seconds,
            )
            detection_dispatcher: AsyncResultDispatcher[InferenceResult] = (
                AsyncResultDispatcher(
                    handler,
                    channel="detection",
                    maxsize=result_dispatch_queue_maxsize,
                    min_interval_seconds=result_dispatch_min_interval_seconds,
                    close_timeout_seconds=result_dispatch_close_timeout_seconds,
                )
            )
            dispatchers.append(detection_dispatcher)
            handler = detection_dispatcher

        if overlay_dispatch_enabled:
            # **오버레이는 저장 경로와 갈라진다.** 저장은 위 간격(기본 1초)으로 묶여
            # 있고 건당 비용도 크지만, 화면의 상자는 추론이 만든 만큼 따라와야 한다.
            # tee는 소비자 스레드에서 돌지만 둘 다 큐에 넣고 즉시 돌아온다.
            logger.info(
                "bbox overlay를 저장과 분리해 FastAPI(%s)로 보낸다. 카메라당 최소 간격 %.2f초.",
                fastapi_url,
                overlay_dispatch_min_interval_seconds,
            )
            overlay_dispatcher: AsyncResultDispatcher[InferenceResult] = (
                AsyncResultDispatcher(
                    FastAPIOverlayHandler(
                        fastapi_url, timeout_seconds=overlay_timeout_seconds
                    ),
                    channel="overlay",
                    maxsize=result_dispatch_queue_maxsize,
                    min_interval_seconds=overlay_dispatch_min_interval_seconds,
                    close_timeout_seconds=result_dispatch_close_timeout_seconds,
                )
            )
            dispatchers.append(overlay_dispatcher)
            # 오버레이를 먼저 넣는다. 지연에 민감한 쪽을 앞에 둔다.
            handler = _tee_handlers(overlay_dispatcher, handler)

    coordinator: (
        IdentityHandoverResultHandler | RefreshingIdentityHandoverResultHandler | None
    ) = None
    if identity_handover_config_url is not None:
        logger.info(
            "FastAPI 인계 ROI 설정을 %.1f초마다 갱신한다.",
            identity_handover_config_refresh_seconds,
        )
        coordinator = RefreshingIdentityHandoverResultHandler(
            identity_handover_routes,
            provider=HttpIdentityHandoverRouteProvider(
                identity_handover_config_url,
                timeout_seconds=identity_handover_config_timeout_seconds,
            ),
            inner=handler,
            refresh_seconds=identity_handover_config_refresh_seconds,
            maximum_delay_seconds=identity_handover_max_delay_seconds,
            clock_skew_seconds=identity_handover_clock_skew_seconds,
            track_stale_seconds=identity_handover_track_stale_seconds,
            minimum_identity_confidence=identity_handover_min_confidence,
            identity_track_recovery_enabled=identity_track_recovery_enabled,
            available_camera_ids=available_camera_ids,
            entry_camera_ids=entry_camera_ids,
            classroom_camera_ids=classroom_camera_ids,
        )
    elif identity_handover_routes:
        logger.info(
            "입구 → 교실 CCTV 신원 인계 route %d개를 켠다.",
            len(identity_handover_routes),
        )
        coordinator = IdentityHandoverResultHandler(
            identity_handover_routes,
            inner=handler,
            maximum_delay_seconds=identity_handover_max_delay_seconds,
            clock_skew_seconds=identity_handover_clock_skew_seconds,
            track_stale_seconds=identity_handover_track_stale_seconds,
            minimum_identity_confidence=identity_handover_min_confidence,
            identity_track_recovery_enabled=identity_track_recovery_enabled,
        )

    if coordinator is not None:
        handler = coordinator

    # CCTV 호출 순서의 가장 바깥이다. 사람 track_id를 먼저 만든 뒤 coordinator가
    # 입구 얼굴 후보를 CCTV의 같은 track에 잠근다. 입구 경로는 이 체인을 타지 않는다.
    if person_tracking_config is not None:
        logger.info("카메라별 사람 ByteTrack을 켠다.")
        handler = ByteTrackResultHandler(
            person_tracking_config,
            camera_ids=person_tracking_camera_ids,
            inner=handler,
            expired_track_handler=(
                coordinator.expire_classroom_tracks if coordinator is not None else None
            ),
            internal_track_handler=(
                coordinator.observe_classroom_tracking
                if coordinator is not None
                else None
            ),
            transition_handler=(
                coordinator.handle_track_transitions
                if coordinator is not None
                else None
            ),
        )

    # 호출 순서의 가장 바깥에서 raw 결과를 먼저 기록한다. 따라서 ByteTrack·신원 인계·
    # 오버레이·스냅샷이 결과를 바꾸기 전의 모델 NMS 출력을 보존한다.
    if settings.person_detection_trace_enabled:
        model_sha256: str | None = None
        if person_model_contract is not None:
            candidate = person_model_contract.get("model_sha256")
            if isinstance(candidate, str):
                model_sha256 = candidate
        try:
            recorder = PersonDetectionTraceRecorder(
                settings.person_detection_trace_directory,
                model_sha256=model_sha256,
                confidence_threshold=settings.inference_confidence_threshold,
                image_size=settings.inference_image_size,
                target_class_ids=settings.inference_target_class_ids,
                max_seconds=settings.person_detection_trace_max_seconds,
                max_frames=settings.person_detection_trace_max_frames,
                retention_hours=settings.person_detection_trace_retention_hours,
            )
        except OSError as error:
            logger.warning(
                "익명 사람 탐지 trace를 준비하지 못해 끄고 계속한다: %s", error
            )
        else:
            logger.info(
                "익명 사람 탐지 trace를 켠다: 최대 %.0f초/%d프레임, 보존 %.0f시간.",
                settings.person_detection_trace_max_seconds,
                settings.person_detection_trace_max_frames,
                settings.person_detection_trace_retention_hours,
            )
            handler = PersonDetectionTraceHandler(recorder, inner=handler)
    entry_handler: EntryResultHandler | None = None
    if face_identity_url is not None:
        observe_entry: EntryResultHandler = (
            coordinator.observe_entry
            if coordinator is not None
            else _discard_entry_result
        )
        if fastapi_url is None:
            entry_handler = observe_entry
        elif result_dispatch_enabled:
            logger.info(
                "입구 얼굴 관측을 FastAPI(%s)에 저장한다. 전송은 별도 스레드가 맡는다.",
                fastapi_url,
            )
            # inner를 비워 전송만 남긴다. 인계 관측은 아래에서 소비자 스레드가 먼저 한다.
            entry_dispatcher: AsyncResultDispatcher[EntryFaceObservationBatch] = (
                AsyncResultDispatcher(
                    FastAPIEntryIdentityEventHandler(
                        fastapi_url, inner=_discard_entry_result
                    ),
                    channel="entry",
                    maxsize=result_dispatch_queue_maxsize,
                    min_interval_seconds=result_dispatch_min_interval_seconds,
                    close_timeout_seconds=result_dispatch_close_timeout_seconds,
                )
            )
            dispatchers.append(entry_dispatcher)
            entry_handler = _sequence_entry_handlers(observe_entry, entry_dispatcher)
            if overlay_dispatch_enabled:
                # CCTV와 같은 이유로 화면용 상자를 저장과 갈라 보낸다(결정 0047).
                # 저장 채널은 전송 간격에 묶여 있어 화면 갱신이 그만큼 성겨진다.
                logger.info("입구 얼굴 상자도 저장과 분리해 보낸다.")
                entry_overlay_dispatcher: AsyncResultDispatcher[
                    EntryFaceObservationBatch
                ] = AsyncResultDispatcher(
                    FastAPIEntryOverlayHandler(
                        fastapi_url, timeout_seconds=overlay_timeout_seconds
                    ),
                    channel="entry-overlay",
                    maxsize=result_dispatch_queue_maxsize,
                    min_interval_seconds=overlay_dispatch_min_interval_seconds,
                    close_timeout_seconds=result_dispatch_close_timeout_seconds,
                )
                dispatchers.append(entry_overlay_dispatcher)
                # 오버레이를 먼저 넣는다. 지연에 민감한 쪽을 앞에 둔다.
                entry_handler = _sequence_entry_handlers(
                    entry_overlay_dispatcher, entry_handler
                )
        else:
            logger.info("입구 얼굴 관측을 FastAPI(%s)에 저장한다.", fastapi_url)
            entry_handler = FastAPIEntryIdentityEventHandler(
                fastapi_url,
                inner=observe_entry,
            )
    return handler, entry_handler, tuple(dispatchers)


def build_runner(
    *,
    stream_settings: StreamSettings,
    inference_settings: InferenceSettings,
    pipeline_settings: PipelineSettings,
) -> PipelineRunner:
    """설정에서 파이프라인을 조립한다. 모델은 여기서 한 번만 로딩한다."""
    shutdown_event = threading.Event()
    camera_sources = stream_settings.camera_sources

    if pipeline_settings.person_tracking_enabled and not any(
        class_name.casefold() == "person"
        for class_name in inference_settings.inference_target_class_ids.values()
    ):
        raise ValueError(
            "ByteTrack을 켜려면 INFERENCE_TARGET_CLASS_IDS에 person 클래스가 필요합니다."
        )
    if (
        pipeline_settings.person_tracking_enabled
        and inference_settings.inference_confidence_threshold
        >= pipeline_settings.bytetrack_high_confidence_threshold
    ):
        raise ValueError(
            "ByteTrack 2단계 매칭을 유지하려면 INFERENCE_CONFIDENCE_THRESHOLD가 "
            "BYTETRACK_HIGH_CONFIDENCE_THRESHOLD보다 낮아야 합니다."
        )

    # 가중치와 전처리 계약이 다르면 모델을 로딩하기 전에 기동을 거부한다.
    person_model_contract = verify_person_model_contract(
        inference_settings.model_path,
        inference_settings.model_contract_path,
        inference_settings.inference_target_class_ids,
        inference_settings.inference_image_size,
    )
    # 모델 로딩은 프로세스 시작 시 1회다. 프레임마다 불러오면 추론이 멈춘다.
    detector = Yolo8nDetector(
        model_path=inference_settings.model_path,
        device=inference_settings.inference_device,
        confidence_threshold=inference_settings.inference_confidence_threshold,
        image_size=inference_settings.inference_image_size,
        target_class_ids=inference_settings.inference_target_class_ids,
    )
    # FASTAPI_URL은 .env·환경변수로 명시해야만 전송을 켠다. 필드에 기본값이 있어
    # 값이 채워져 있는 것만으로는 "설정했다"를 판단할 수 없다. 명시 여부는 pydantic이
    # 기록한 model_fields_set으로 본다. 빈 문자열로 끈 것도 설정으로 치지 않는다.
    fastapi_url: str | None = None
    if "fastapi_url" in pipeline_settings.model_fields_set:
        candidate = pipeline_settings.fastapi_url.strip()
        if candidate:
            fastapi_url = candidate
    face_identity_url: str | None = None
    if "face_identity_url" in pipeline_settings.model_fields_set:
        candidate = pipeline_settings.face_identity_url.strip()
        if candidate:
            face_identity_url = candidate

    handover_routes = pipeline_settings.parsed_identity_handover_routes
    configured_camera_ids = {source.camera_id for source in camera_sources}
    handover_camera_ids = {
        camera_id
        for route in handover_routes
        for camera_id in (route.entry_camera_id, route.classroom_camera_id)
    }
    missing_camera_ids = handover_camera_ids - configured_camera_ids
    if missing_camera_ids:
        raise ValueError(
            "신원 인계 route의 카메라가 STREAM_SOURCES에 없습니다: "
            + ", ".join(sorted(missing_camera_ids))
        )
    face_identity_camera_ids = pipeline_settings.parsed_face_identity_camera_ids
    if face_identity_url is not None:
        if fastapi_url is None:
            raise ValueError(
                "입구 얼굴 관측을 저장하려면 FASTAPI_URL을 명시해야 합니다."
            )
        missing_face_camera_ids = face_identity_camera_ids - configured_camera_ids
        if missing_face_camera_ids:
            raise ValueError(
                "FACE_IDENTITY_CAMERA_IDS의 카메라가 STREAM_SOURCES에 없습니다: "
                + ", ".join(sorted(missing_face_camera_ids))
            )
    configured_tracking_ids = pipeline_settings.parsed_person_tracking_camera_ids
    tracking_camera_ids = (
        configured_tracking_ids
        if configured_tracking_ids is not None
        else frozenset(configured_camera_ids - face_identity_camera_ids)
    )
    if pipeline_settings.person_tracking_enabled:
        missing_tracking_ids = tracking_camera_ids - configured_camera_ids
        if missing_tracking_ids:
            raise ValueError(
                "PERSON_TRACKING_CAMERA_IDS의 카메라가 STREAM_SOURCES에 없습니다: "
                + ", ".join(sorted(missing_tracking_ids))
            )
        overlap = face_identity_camera_ids & tracking_camera_ids
        if overlap:
            raise ValueError(
                "얼굴 전용 카메라와 사람 추적 카메라는 겹칠 수 없습니다: "
                + ", ".join(sorted(overlap))
            )
        if face_identity_url is not None:
            unassigned = configured_camera_ids - (
                face_identity_camera_ids | tracking_camera_ids
            )
            if unassigned:
                raise ValueError(
                    "모든 STREAM_SOURCES 카메라는 얼굴 전용 또는 사람 추적 역할이 "
                    "필요합니다: " + ", ".join(sorted(unassigned))
                )
        classroom_route_ids = {route.classroom_camera_id for route in handover_routes}
        untracked_classroom_ids = classroom_route_ids - tracking_camera_ids
        if untracked_classroom_ids:
            raise ValueError(
                "신원 인계 route의 교실 카메라에는 ByteTrack이 필요합니다: "
                + ", ".join(sorted(untracked_classroom_ids))
            )

    # 얼굴 HTTP와 YOLO를 같은 소비자에서 순서대로 실행하면 입구 요청 지연만큼 CCTV
    # track 갱신도 멈춘다. 역할마다 최신 프레임 버퍼와 소비자를 따로 둔다.
    tracking_buffer = FrameBuffer(
        maxsize=max(pipeline_settings.frame_buffer_maxsize, len(tracking_camera_ids)),
        per_camera=True,
    )
    entry_buffer = (
        FrameBuffer(
            maxsize=max(
                pipeline_settings.frame_buffer_maxsize,
                len(face_identity_camera_ids),
            ),
            per_camera=True,
        )
        if face_identity_url is not None
        else None
    )
    frame_buffers_by_camera_id = {
        camera_id: tracking_buffer for camera_id in tracking_camera_ids
    }
    if entry_buffer is not None:
        frame_buffers_by_camera_id.update(
            {camera_id: entry_buffer for camera_id in face_identity_camera_ids}
        )
    sample_intervals_by_camera_id = {
        camera_id: pipeline_settings.person_tracking_sample_interval_frames
        for camera_id in tracking_camera_ids
    }
    sample_intervals_by_camera_id.update(
        {
            camera_id: pipeline_settings.face_identity_sample_interval_frames
            for camera_id in face_identity_camera_ids
        }
    )

    person_tracking_config = (
        ByteTrackConfig(
            high_confidence_threshold=(
                pipeline_settings.bytetrack_high_confidence_threshold
            ),
            low_confidence_threshold=(
                pipeline_settings.bytetrack_low_confidence_threshold
            ),
            new_track_threshold=pipeline_settings.bytetrack_new_track_threshold,
            first_match_iou_threshold=(
                pipeline_settings.bytetrack_first_match_iou_threshold
            ),
            second_match_iou_threshold=(
                pipeline_settings.bytetrack_second_match_iou_threshold
            ),
            track_buffer_frames=pipeline_settings.bytetrack_buffer_frames,
            kalman_enabled=pipeline_settings.bytetrack_kalman_enabled,
            track_lifecycle_enabled=(
                pipeline_settings.person_track_lifecycle_enabled
            ),
            person_detection_postprocess_enabled=(
                pipeline_settings.person_detection_postprocess_enabled
            ),
            duplicate_iou_threshold=(
                pipeline_settings.person_detection_duplicate_iou_threshold
            ),
            duplicate_ios_threshold=(
                pipeline_settings.person_detection_duplicate_ios_threshold
            ),
        )
        if pipeline_settings.person_tracking_enabled
        else None
    )
    result_handler, entry_result_handler, result_dispatchers = build_result_handlers(
        inference_settings,
        person_model_contract=person_model_contract,
        fastapi_url=fastapi_url,
        face_identity_url=face_identity_url,
        person_tracking_config=person_tracking_config,
        person_tracking_camera_ids=tracking_camera_ids,
        identity_handover_routes=handover_routes,
        identity_handover_config_url=(
            fastapi_url
            if fastapi_url is not None
            and face_identity_url is not None
            and pipeline_settings.person_tracking_enabled
            and pipeline_settings.identity_handover_dynamic_config_enabled
            else None
        ),
        identity_handover_config_refresh_seconds=(
            pipeline_settings.identity_handover_config_refresh_seconds
        ),
        identity_handover_config_timeout_seconds=(
            pipeline_settings.identity_handover_config_timeout_seconds
        ),
        identity_handover_max_delay_seconds=(
            pipeline_settings.identity_handover_max_delay_seconds
        ),
        identity_handover_clock_skew_seconds=(
            pipeline_settings.identity_handover_clock_skew_seconds
        ),
        identity_handover_track_stale_seconds=(
            pipeline_settings.identity_handover_track_stale_seconds
        ),
        identity_handover_min_confidence=(
            pipeline_settings.identity_handover_min_confidence
        ),
        identity_track_recovery_enabled=(
            pipeline_settings.identity_track_recovery_enabled
        ),
        available_camera_ids=frozenset(configured_camera_ids),
        entry_camera_ids=face_identity_camera_ids,
        classroom_camera_ids=tracking_camera_ids,
        result_dispatch_enabled=pipeline_settings.result_dispatch_enabled,
        result_dispatch_queue_maxsize=pipeline_settings.result_dispatch_queue_maxsize,
        result_dispatch_min_interval_seconds=(
            pipeline_settings.result_dispatch_min_interval_seconds
        ),
        overlay_dispatch_enabled=pipeline_settings.overlay_dispatch_enabled,
        overlay_dispatch_min_interval_seconds=(
            pipeline_settings.overlay_dispatch_min_interval_seconds
        ),
        overlay_timeout_seconds=pipeline_settings.overlay_timeout_seconds,
        result_dispatch_close_timeout_seconds=(
            pipeline_settings.result_dispatch_close_timeout_seconds
        ),
    )

    entry_processor = (
        EntryFaceProcessor(
            HttpFaceIdentifier(
                face_identity_url,
                timeout_seconds=pipeline_settings.face_identity_timeout_seconds,
                jpeg_quality=pipeline_settings.face_identity_jpeg_quality,
            )
        )
        if face_identity_url is not None
        else None
    )

    inference_processor = InferenceProcessor(detector)
    consumers: list[InferenceConsumer] = []
    consumer_buffers: list[FrameBuffer] = []
    if tracking_camera_ids:
        consumers.append(
            InferenceConsumer(
                frame_buffer=tracking_buffer,
                processor=inference_processor,
                shutdown_event=shutdown_event,
                poll_timeout_seconds=pipeline_settings.inference_poll_timeout_seconds,
                max_consecutive_failures=(
                    pipeline_settings.inference_max_consecutive_failures
                ),
                result_handler=result_handler,
            )
        )
        consumer_buffers.append(tracking_buffer)
    if entry_buffer is not None:
        assert entry_processor is not None
        assert entry_result_handler is not None
        consumers.append(
            InferenceConsumer(
                frame_buffer=entry_buffer,
                processor=inference_processor,
                shutdown_event=shutdown_event,
                poll_timeout_seconds=pipeline_settings.inference_poll_timeout_seconds,
                max_consecutive_failures=(
                    pipeline_settings.inference_max_consecutive_failures
                ),
                result_handler=result_handler,
                entry_processor=entry_processor,
                entry_camera_ids=face_identity_camera_ids,
                entry_result_handler=entry_result_handler,
            )
        )
        consumer_buffers.append(entry_buffer)
    if not consumers:
        raise ValueError("추론할 카메라 역할이 하나 이상 필요합니다.")

    stream_worker = StreamWorker(
        stream_settings,
        publisher=build_publisher(stream_settings),
        frame_buffer=consumer_buffers[0],
        frame_buffers_by_camera_id=frame_buffers_by_camera_id,
        sample_intervals_by_camera_id=sample_intervals_by_camera_id,
        shutdown_event=shutdown_event,
    )
    return PipelineRunner(
        stream_worker=stream_worker,
        consumer=consumers[0],
        frame_buffer=consumer_buffers[0],
        additional_consumers=consumers[1:],
        additional_frame_buffers=consumer_buffers[1:],
        result_dispatchers=result_dispatchers,
        shutdown_event=shutdown_event,
    )


def enable_metrics(runner: PipelineRunner, settings: PipelineSettings) -> None:
    """지표 노출을 켠다. 실패해도 파이프라인은 그대로 시작한다.

    **조립(`build_runner`)이 아니라 여기서 부른다.** 버퍼 collector는 전역
    레지스트리에 한 번만 들어갈 수 있는데, 조립 함수는 테스트가 여러 번 호출한다.
    실행 진입점은 프로세스당 한 번뿐이라 등록 지점으로 맞다.
    """
    if not settings.metrics_enabled:
        logger.info("지표 노출이 꺼져 있다(METRICS_ENABLED=false).")
        return

    register_frame_buffer(runner.frame_buffers)
    start_metrics_server(host=settings.metrics_host, port=settings.metrics_port)


def _install_signal_handlers(runner: PipelineRunner) -> None:
    def handle_signal(signal_number: int, frame: FrameType | None) -> None:
        logger.info(
            "종료 신호(%s)를 받아 정리를 시작한다", signal.Signals(signal_number).name
        )
        runner.request_shutdown()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)


def main() -> int:
    use_utf8_console()

    try:
        # 조립 실행에서는 워커별 .env.*가 아니라 pipeline/.env.{APP_ENV} 하나만 읽는다.
        # 일반 설정·판정 기준값(config/settings.yml)은 각자 자기 디렉터리 것을 그대로 읽는다.
        stream_settings = StreamSettings(_env_file=PIPELINE_ENV_FILE)  # type: ignore[call-arg]
        inference_settings = InferenceSettings(_env_file=PIPELINE_ENV_FILE)  # type: ignore[call-arg]
        pipeline_settings = PipelineSettings(_env_file=PIPELINE_ENV_FILE)  # type: ignore[call-arg]
    except ValidationError as error:
        logging.basicConfig(level="ERROR")
        logger.error(
            "설정이 올바르지 않아 시작할 수 없다:\n%s", format_validation_error(error)
        )
        return 1

    configure_logging(stream_settings.log_level)

    if stream_settings.recording_enabled or stream_settings.frame_capture_enabled:
        logger.warning(
            "영상·프레임 로컬 저장이 켜져 있다. 개발용 임시 수단이며 보존 기간이 "
            "정해져 있지 않다. 저장물을 커밋하지 않는다."
        )

    try:
        runner = build_runner(
            stream_settings=stream_settings,
            inference_settings=inference_settings,
            pipeline_settings=pipeline_settings,
        )
    except (ImportError, OSError, ValueError) as error:
        # ultralytics 미설치나 가중치 파일을 찾지 못한 경우가 대부분이다.
        logger.error("추론 모델을 준비하지 못했다: %s", error)
        return 1

    enable_metrics(runner, pipeline_settings)
    _install_signal_handlers(runner)

    try:
        return runner.run()
    except StreamWorkerError as error:
        logger.error("파이프라인을 시작하지 못했다: %s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
