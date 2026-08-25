"""SCRFD 얼굴 검출 내부 HTTP 서비스."""

from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Literal

import cv2
import mediapipe as mp
import numpy as np
from dotenv import load_dotenv
from face_identification import (
    FaceGalleryUnavailable,
    FaceIdentificationConfig,
    FaceIdentificationRuntime,
    FaceModelMetadata,
    MongoFaceGalleryLoader,
)
from face_recognizer import (
    FaceRecognizerConfig,
    build_face_recognizer,
    load_face_recognizer_config,
)
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from insightface.model_zoo import get_model
from insightface.utils import face_align

# **상대 import를 쓰지 않는다.** 컨테이너는 `uvicorn app:app`으로 띄우므로
# app.py가 패키지의 일부가 아니다(Dockerfile). 테스트는 `deeplearning.tests.conftest`가
# 이 디렉터리를 sys.path에 넣어 같은 이름으로 찾게 한다.
from metrics import (
    install_session_gauge,
    observe_analysis_stage,
    record_analysis_request,
    record_embedding_request,
    record_identification_observations,
    record_identification_request,
    render_metrics,
)
from pydantic import BaseModel

# 로컬 직접 실행은 deeplearning/.env를 읽는다. 컨테이너에서는 별도의 Docker env 파일을
# Compose가 먼저 주입하므로 override=False가 그 값을 보존한다. 로컬 .env는 이미지 안에
# 복사하지 않는다(Dockerfile·.dockerignore).
load_dotenv(Path(__file__).resolve().with_name(".env"), override=False)

DEFAULT_FACE_MODEL_METADATA = FaceModelMetadata(
    model_name="arcface",
    model_version="insightface-buffalo_l-w600k_r50-v0.7",
    preprocessing_version="insightface-norm-crop-112-v1",
)


class AnalysisResponse(BaseModel):
    face_count: int
    detection_confidence: float
    face_size_ratio: float
    centered: bool
    yaw_degrees: float = 0
    pitch_degrees: float = 0
    roll_degrees: float = 0
    blur_score: float = 1
    brightness_score: float = 1
    landmark_confidence: float = 1
    occlusion_score: float = 0
    duplicate_score: float = 0
    motion_speed_dps: float = 0


class EmbeddingResponse(BaseModel):
    vector: list[float]
    dimension: int
    normalized: bool
    model_name: str
    model_version: str
    preprocessing_version: str


class FaceObservationResponse(BaseModel):
    face_track_id: str
    face_bbox: tuple[int, int, int, int]
    detection_confidence: float
    identity_status: Literal["REGISTERED", "UNKNOWN", "UNCERTAIN"]
    student_id: str | None = None
    similarity: float | None = None
    margin: float | None = None
    quality: float
    observation_count: int
    rejected_reason: str | None = None


class FaceIdentificationResponse(BaseModel):
    observations: list[FaceObservationResponse]


@dataclass(frozen=True)
class FrameHistory:
    captured_at: float
    yaw: float
    pitch: float
    fingerprint: int


@dataclass(frozen=True)
class FingerprintHistory:
    yaw: float
    pitch: float
    fingerprint: int


_frame_history: dict[str, FrameHistory] = {}
_fingerprint_history: dict[str, list[FingerprintHistory]] = {}
_history_lock = RLock()


def _active_session_count() -> int:
    with _history_lock:
        return len(_frame_history)


# **세션이 정리되지 않는 것을 잡기 위한 연결이다.** 브라우저가 등록 화면을 그냥 닫으면
# DELETE가 오지 않아 위 두 딕셔너리에 항목이 남는다. 스크랩 시점에만 세므로 요청
# 경로에는 아무것도 추가되지 않는다.
install_session_gauge(_active_session_count)


def _model_path() -> Path:
    value = os.environ.get("FACE_DETECTION_MODEL_PATH")
    if not value:
        raise RuntimeError("FACE_DETECTION_MODEL_PATH가 필요합니다.")
    path = Path(value)
    if not path.is_file():
        raise RuntimeError("SCRFD 모델 파일을 찾을 수 없습니다.")
    return path


def _landmarker_path() -> Path:
    value = os.environ.get("FACE_LANDMARKER_MODEL_PATH")
    if not value:
        raise RuntimeError("FACE_LANDMARKER_MODEL_PATH가 필요합니다.")
    path = Path(value)
    if not path.is_file():
        raise RuntimeError("MediaPipe Face Landmarker 모델 파일을 찾을 수 없습니다.")
    return path


def _environment_bool(name: str, default: str = "false") -> bool:
    value = os.environ.get(name, default).strip().lower()
    if value not in {"true", "false"}:
        raise RuntimeError(f"{name}은 true 또는 false여야 합니다.")
    return value == "true"


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name}가 필요합니다.")
    return value


def _face_detection_threshold() -> float:
    try:
        value = float(os.environ.get("FACE_DETECTION_THRESHOLD", "0.6"))
    except ValueError:
        raise RuntimeError("FACE_DETECTION_THRESHOLD는 실수여야 합니다.") from None
    if not 0.0 <= value <= 1.0:
        raise RuntimeError("FACE_DETECTION_THRESHOLD는 0과 1 사이여야 합니다.")
    return value


def _identity_thresholds(
    metadata: FaceModelMetadata | None = None,
) -> tuple[float, float, float]:
    metadata = metadata or DEFAULT_FACE_MODEL_METADATA
    configured_path = os.environ.get("FACE_IDENTITY_THRESHOLD_FILE", "").strip()
    if not configured_path:
        return (
            float(_required_environment("FACE_IDENTITY_SIMILARITY_THRESHOLD")),
            float(_required_environment("FACE_IDENTITY_MARGIN_THRESHOLD")),
            float(_required_environment("FACE_IDENTITY_TRACK_SIMILARITY_THRESHOLD")),
        )
    path = Path(configured_path)
    if not path.is_file():
        raise RuntimeError("얼굴 식별 임계값 파일을 찾을 수 없습니다.")
    try:
        values = json.loads(path.read_text(encoding="utf-8"))
        if (
            values.get("model_name") != metadata.model_name
            or values.get("model_version") != metadata.model_version
            or values.get("preprocessing_version") != metadata.preprocessing_version
        ):
            raise ValueError
        return (
            float(values["similarity_threshold"]),
            float(values["margin_threshold"]),
            float(values["track_similarity_threshold"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise RuntimeError(
            "얼굴 식별 임계값 파일이 현재 모델과 호환되지 않습니다."
        ) from None


def _build_face_identification_runtime(
    *, detector: Any, recognizer: Any, recognizer_config: FaceRecognizerConfig
) -> FaceIdentificationRuntime | None:
    if not _environment_bool("FACE_IDENTIFICATION_ENABLED"):
        return None
    similarity_threshold, margin_threshold, track_similarity_threshold = (
        _identity_thresholds(recognizer_config.metadata)
    )
    gallery_loader = MongoFaceGalleryLoader(
        database_url=_required_environment("FACE_GALLERY_DATABASE_URL"),
        database_name=_required_environment("FACE_GALLERY_DATABASE_NAME"),
        collection_name=recognizer_config.collection_name,
        expected_metadata=recognizer_config.metadata,
        timeout_seconds=float(os.environ.get("FACE_GALLERY_TIMEOUT_SECONDS", "5")),
    )
    config = FaceIdentificationConfig(
        # 평가 하네스로 고른 값만 넣게 한다. 근거 없는 기본값으로 이름을 붙이지 않는다.
        similarity_threshold=similarity_threshold,
        margin_threshold=margin_threshold,
        track_similarity_threshold=track_similarity_threshold,
        gallery_refresh_seconds=float(
            os.environ.get("FACE_GALLERY_REFRESH_SECONDS", "30")
        ),
        detection_threshold=_face_detection_threshold(),
        identity_min_detection_confidence=float(
            os.environ.get("FACE_IDENTITY_MIN_DETECTION_CONFIDENCE", "0.6")
        ),
        minimum_face_size=int(os.environ.get("FACE_MINIMUM_SIZE", "40")),
        preferred_face_size=int(os.environ.get("FACE_PREFERRED_SIZE", "112")),
        minimum_blur_score=float(os.environ.get("FACE_MINIMUM_BLUR_SCORE", "20")),
        preferred_blur_score=float(os.environ.get("FACE_PREFERRED_BLUR_SCORE", "100")),
        uncertain_quality_threshold=float(
            os.environ.get("FACE_UNCERTAIN_QUALITY_THRESHOLD", "0.45")
        ),
        use_flip_tta=_environment_bool("FACE_USE_FLIP_TTA", "true"),
        tta_similarity_band=float(os.environ.get("FACE_TTA_SIMILARITY_BAND", "0.08")),
        tta_margin_band=float(os.environ.get("FACE_TTA_MARGIN_BAND", "0.06")),
        tracker_history_size=int(os.environ.get("FACE_IDENTITY_HISTORY_SIZE", "12")),
        tracker_minimum_observations=int(
            os.environ.get("FACE_IDENTITY_MINIMUM_OBSERVATIONS", "4")
        ),
        tracker_stale_frames=int(
            os.environ.get("FACE_IDENTITY_TRACK_STALE_FRAMES", "30")
        ),
    )
    return FaceIdentificationRuntime(
        detector=detector,
        recognizer=recognizer,
        gallery_loader=gallery_loader,
        config=config,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    recognizer_config = load_face_recognizer_config()
    detector = get_model(str(_model_path()), providers=["CPUExecutionProvider"])
    detector.prepare(
        ctx_id=-1,
        input_size=(640, 640),
        det_thresh=_face_detection_threshold(),
    )
    app.state.detector = detector
    recognizer = build_face_recognizer(recognizer_config)
    app.state.recognizer = recognizer
    app.state.face_recognizer_config = recognizer_config
    app.state.face_model_metadata = recognizer_config.metadata
    app.state.face_identification_runtime = _build_face_identification_runtime(
        detector=detector,
        recognizer=recognizer,
        recognizer_config=recognizer_config,
    )
    app.state.landmarker = mp.tasks.vision.FaceLandmarker.create_from_options(
        mp.tasks.vision.FaceLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(_landmarker_path())),
            num_faces=1,
            min_face_detection_confidence=0.6,
            min_face_presence_confidence=0.6,
            output_facial_transformation_matrixes=True,
        )
    )
    try:
        yield
    finally:
        app.state.landmarker.close()


app = FastAPI(title="Face Analysis Internal Service", lifespan=lifespan)


def _inside_guide(
    bbox: tuple[float, float, float, float], width: int, height: int
) -> bool:
    left, top, right, bottom = bbox
    frame_margin_x = width * 0.03
    frame_margin_y = height * 0.03
    if (
        left < frame_margin_x
        or top < frame_margin_y
        or right > width - frame_margin_x
        or bottom > height - frame_margin_y
    ):
        return False
    radius_x = width * 0.28
    radius_y = height * 0.36
    center_x = width * 0.5
    center_y = height * 0.5
    face_center_x = (left + right) / 2
    face_center_y = (top + bottom) / 2
    return ((face_center_x - center_x) / radius_x) ** 2 + (
        (face_center_y - center_y) / radius_y
    ) ** 2 <= 1


def _head_pose(
    request: Request, image: np.ndarray
) -> tuple[float, float, float, float, float]:
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    media_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    result = request.app.state.landmarker.detect(media_image)
    if not result.face_landmarks or not result.facial_transformation_matrixes:
        return 0.0, 0.0, 0.0, 0.0, 1.0
    matrix = np.asarray(result.facial_transformation_matrixes[0], dtype=np.float64)
    pitch, yaw, roll = cv2.RQDecomp3x3(matrix[:3, :3])[0]
    key_landmark_indices = (1, 13, 14, 33, 133, 263, 362)
    landmarks = result.face_landmarks[0]
    outside_count = sum(
        not (0 <= landmarks[index].x <= 1 and 0 <= landmarks[index].y <= 1)
        for index in key_landmark_indices
    )
    occlusion_score = outside_count / len(key_landmark_indices)
    return float(yaw), float(pitch), float(roll), 1.0, occlusion_score


def _face_quality(image: np.ndarray) -> tuple[float, float, int]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur_score = min(1.0, float(cv2.Laplacian(gray, cv2.CV_64F).var()) / 150.0)
    mean_brightness = float(gray.mean())
    if 45 <= mean_brightness <= 220:
        brightness_score = 1.0
    elif mean_brightness < 45:
        brightness_score = max(0.0, (mean_brightness - 10) / 35)
    else:
        brightness_score = max(0.0, (250 - mean_brightness) / 30)
    small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits = (small[:, 1:] > small[:, :-1]).reshape(-1)
    fingerprint = sum(int(bit) << index for index, bit in enumerate(bits))
    return blur_score, brightness_score, fingerprint


def _temporal_quality(
    enrollment_id: str,
    *,
    yaw: float,
    pitch: float,
    fingerprint: int,
    captured_at: float,
) -> tuple[float, float]:
    with _history_lock:
        previous = _frame_history.get(enrollment_id)
        _frame_history[enrollment_id] = FrameHistory(
            captured_at=captured_at,
            yaw=yaw,
            pitch=pitch,
            fingerprint=fingerprint,
        )
        fingerprints = _fingerprint_history.setdefault(enrollment_id, [])
        comparable = [
            item
            for item in fingerprints
            if max(abs(yaw - item.yaw), abs(pitch - item.pitch)) < 2.5
        ]
        fingerprints.append(FingerprintHistory(yaw, pitch, fingerprint))
        if len(fingerprints) > 120:
            del fingerprints[:-120]
    if previous is None:
        return 0.0, 0.0
    elapsed = max(captured_at - previous.captured_at, 0.001)
    pose_delta = max(abs(yaw - previous.yaw), abs(pitch - previous.pitch))
    motion_speed = pose_delta / elapsed
    duplicate_score = max(
        (
            1.0 - (fingerprint ^ item.fingerprint).bit_count() / 64
            for item in comparable
        ),
        default=0.0,
    )
    return motion_speed, duplicate_score


@app.post("/internal/face-analysis", response_model=AnalysisResponse)
async def analyze(request: Request) -> AnalysisResponse:
    """프레임 한 장을 분석한다. 실패로 끝난 것도 사유별로 센다.

    본문을 `_analyze`로 뺀 이유는 **예상하지 못한 실패(500)를 세기 위해서다.** 세지
    않으면 대시보드에는 "느려졌다"만 보이고 "실패하고 있다"는 보이지 않는다.
    """
    try:
        return await _analyze(request)
    except HTTPException:
        # 사유별 계측은 `_analyze` 안에서 이미 끝났다. 여기서 또 세면 두 번 센다.
        raise
    except Exception:
        record_analysis_request("error")
        raise


async def _analyze(request: Request) -> AnalysisResponse:
    request_started_at = time.perf_counter()
    enrollment_id = request.headers.get("X-Face-Enrollment-ID")
    if not enrollment_id:
        record_analysis_request("missing_session")
        raise HTTPException(status_code=400, detail="얼굴 등록 세션 ID가 필요합니다.")
    content = await request.body()
    encoded = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    if encoded is None:
        record_analysis_request("bad_image")
        raise HTTPException(status_code=400, detail="JPEG 프레임을 해석할 수 없습니다.")
    detector: Any = request.app.state.detector
    detect_started_at = time.perf_counter()
    detections, _ = detector.detect(encoded, max_num=0)
    observe_analysis_stage("detect", detect_started_at)
    height, width = encoded.shape[:2]
    guide_indices = [
        index
        for index, detection in enumerate(detections)
        if _inside_guide(
            tuple(float(value) for value in detection[:4]),
            width,
            height,
        )
    ]
    face_count = len(guide_indices)
    if face_count == 0:
        # 실패가 아니라 정상적인 결과다. 사용자가 아직 가이드 안에 얼굴을 두지
        # 못했다는 뜻이고, 등록 화면은 그것을 보고 안내를 띄운다.
        record_analysis_request("no_face")
        observe_analysis_stage("total", request_started_at)
        return AnalysisResponse(
            face_count=0,
            detection_confidence=0,
            face_size_ratio=0,
            centered=False,
        )
    primary_index = max(guide_indices, key=lambda index: float(detections[index][4]))
    primary = detections[primary_index]
    left, top, right, bottom, confidence = (float(value) for value in primary[:5])
    area_ratio = max(0.0, right - left) * max(0.0, bottom - top) / (width * height)
    face_crop = encoded[
        max(0, int(top)) : min(height, int(bottom)),
        max(0, int(left)) : min(width, int(right)),
    ]
    quality_started_at = time.perf_counter()
    blur_score, brightness_score, fingerprint = _face_quality(face_crop)
    observe_analysis_stage("quality", quality_started_at)
    # MediaPipe 랜드마크는 SCRFD와 함께 이 경로에서 가장 무거운 두 축이다.
    # 나눠 재지 않으면 느려졌을 때 어느 쪽인지 알 수 없다.
    pose_started_at = time.perf_counter()
    yaw, pitch, roll, landmark_confidence, occlusion_score = _head_pose(
        request, encoded
    )
    observe_analysis_stage("pose", pose_started_at)
    motion_speed, duplicate_score = _temporal_quality(
        enrollment_id,
        yaw=yaw,
        pitch=pitch,
        fingerprint=fingerprint,
        captured_at=time.monotonic(),
    )
    record_analysis_request("ok")
    observe_analysis_stage("total", request_started_at)
    return AnalysisResponse(
        face_count=face_count,
        detection_confidence=confidence,
        face_size_ratio=area_ratio,
        centered=True,
        yaw_degrees=yaw,
        pitch_degrees=pitch,
        roll_degrees=roll,
        blur_score=blur_score,
        brightness_score=brightness_score,
        landmark_confidence=landmark_confidence,
        occlusion_score=occlusion_score,
        duplicate_score=duplicate_score,
        motion_speed_dps=motion_speed,
    )


@app.delete("/internal/face-analysis/sessions/{enrollment_id}", status_code=204)
def discard_session(enrollment_id: str) -> None:
    with _history_lock:
        _frame_history.pop(enrollment_id, None)
        _fingerprint_history.pop(enrollment_id, None)


@app.post("/internal/face-embeddings", response_model=EmbeddingResponse)
async def create_embedding(request: Request) -> EmbeddingResponse:
    """등록 사진 한 장에서 embedding을 만든다. 거절 사유를 나눠 센다.

    사유마다 다른 조치로 이어지기 때문이다 — 얼굴 수는 촬영 환경, 신뢰도는 조명과
    거리, 벡터 무효는 모델 쪽 문제다. 하나로 묶으면 "등록이 안 된다"까지만 남는다.
    """
    started_at = time.perf_counter()
    try:
        return await _create_embedding(request, started_at=started_at)
    except HTTPException:
        raise
    except Exception:
        record_embedding_request("error", started_at)
        raise


async def _create_embedding(
    request: Request, *, started_at: float
) -> EmbeddingResponse:
    content = await request.body()
    image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        record_embedding_request("bad_image", started_at)
        raise HTTPException(status_code=400, detail="JPEG 이미지를 해석할 수 없습니다.")
    detector: Any = request.app.state.detector
    detections, keypoints = detector.detect(image, max_num=0)
    if len(detections) != 1 or keypoints is None or len(keypoints) != 1:
        record_embedding_request("not_single_face", started_at)
        raise HTTPException(status_code=422, detail="정확히 한 명의 얼굴이 필요합니다.")
    if float(detections[0][4]) < 0.6:
        record_embedding_request("low_confidence", started_at)
        raise HTTPException(status_code=422, detail="얼굴 검출 신뢰도가 부족합니다.")
    aligned = face_align.norm_crop(image, landmark=keypoints[0], image_size=112)
    recognizer: Any = request.app.state.recognizer
    vector = np.asarray(recognizer.get_feat(aligned), dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    if vector.size != 512 or not np.isfinite(vector).all() or norm <= 1e-12:
        record_embedding_request("invalid_vector", started_at)
        raise HTTPException(
            status_code=422, detail="유효한 얼굴 embedding을 생성하지 못했습니다."
        )
    normalized = vector / norm
    record_embedding_request("ok", started_at)
    metadata: FaceModelMetadata = getattr(
        request.app.state, "face_model_metadata", DEFAULT_FACE_MODEL_METADATA
    )
    return EmbeddingResponse(
        vector=normalized.tolist(),
        dimension=int(normalized.size),
        normalized=True,
        model_name=metadata.model_name,
        model_version=metadata.model_version,
        preprocessing_version=metadata.preprocessing_version,
    )


@app.post(
    "/internal/face-identifications",
    response_model=FaceIdentificationResponse,
)
async def identify_faces(request: Request) -> FaceIdentificationResponse:
    """입구 카메라 JPEG에서 얼굴을 검출·추적하고 오픈셋 신원을 판정한다."""
    started_at = time.perf_counter()
    metadata: FaceModelMetadata = getattr(
        request.app.state, "face_model_metadata", DEFAULT_FACE_MODEL_METADATA
    )
    model_name: Literal["arcface", "adaface"] = metadata.model_name  # type: ignore[assignment]
    try:
        return await _identify_faces(
            request, started_at=started_at, model_name=model_name
        )
    except HTTPException:
        raise
    except Exception:
        record_identification_request(model_name, "error", started_at)
        raise


async def _identify_faces(
    request: Request,
    *,
    started_at: float,
    model_name: Literal["arcface", "adaface"],
) -> FaceIdentificationResponse:
    runtime: FaceIdentificationRuntime | None = getattr(
        request.app.state, "face_identification_runtime", None
    )
    if runtime is None:
        record_identification_request(model_name, "disabled", started_at)
        raise HTTPException(status_code=503, detail="얼굴 식별 기능이 꺼져 있습니다.")
    camera_id = request.headers.get("X-Camera-ID", "").strip()
    if not camera_id or len(camera_id) > 128:
        record_identification_request(model_name, "invalid_camera", started_at)
        raise HTTPException(status_code=400, detail="카메라 ID가 필요합니다.")
    content = await request.body()
    image = cv2.imdecode(np.frombuffer(content, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        record_identification_request(model_name, "bad_image", started_at)
        raise HTTPException(status_code=400, detail="JPEG 이미지를 해석할 수 없습니다.")
    try:
        identities = runtime.identify(
            camera_id=camera_id,
            image_bgr=image,
        )
    except FaceGalleryUnavailable:
        record_identification_request(model_name, "gallery_unavailable", started_at)
        # DB 주소나 embedding 값은 응답과 로그에 싣지 않는다.
        raise HTTPException(
            status_code=503, detail="얼굴 갤러리를 사용할 수 없습니다."
        ) from None
    response = FaceIdentificationResponse(
        observations=[
            FaceObservationResponse(
                face_track_id=f"face-{identity.track_id}",
                face_bbox=identity.bbox,
                detection_confidence=identity.detection_confidence,
                identity_status=identity.status.name,
                student_id=identity.student_id,
                similarity=identity.similarity,
                margin=identity.margin,
                quality=identity.quality,
                observation_count=identity.observation_count,
                rejected_reason=identity.rejected_reason,
            )
            for identity in identities
        ]
    )
    record_identification_request(model_name, "ok", started_at)
    record_identification_observations(
        model_name,
        [
            identity.status.name.lower()  # type: ignore[misc]
            for identity in identities
        ],
    )
    return response


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
def readiness(request: Request) -> dict[str, str]:
    """모델과, 활성화된 경우 MongoDB 얼굴 갤러리까지 사용할 수 있는지 확인한다."""
    runtime: FaceIdentificationRuntime | None = getattr(
        request.app.state, "face_identification_runtime", None
    )
    metadata: FaceModelMetadata = getattr(
        request.app.state, "face_model_metadata", DEFAULT_FACE_MODEL_METADATA
    )
    common = {
        "active_face_model": metadata.model_name,
        "face_model_version": metadata.model_version,
        "face_preprocessing_version": metadata.preprocessing_version,
    }
    if runtime is None:
        return {
            "status": "ready",
            "face_identification": "disabled",
            **common,
        }
    try:
        gallery_status = runtime.ensure_ready()
    except FaceGalleryUnavailable:
        raise HTTPException(
            status_code=503, detail="얼굴 갤러리를 사용할 수 없습니다."
        ) from None
    return {
        "status": "ready",
        "face_identification": "ready",
        **common,
        "gallery_entries": str(gallery_status.gallery_entries),
        "excluded_gallery_entries": str(gallery_status.excluded_gallery_entries),
        "active_registered_students": str(gallery_status.active_registered_students),
        "missing_gallery_entries": str(gallery_status.missing_gallery_entries),
    }


# **끄면 라우트 자체를 만들지 않는다.** 404를 돌려주는 경로를 남기면 "지표가 있는데
# 지금 실패한 것"과 "이 배포에는 없는 것"이 구분되지 않는다. 값은 기동 시점에 읽는다.
if os.environ.get("METRICS_ENABLED", "true").strip().lower() != "false":

    @app.get("/metrics")
    def metrics() -> Response:
        body, content_type = render_metrics()
        return Response(content=body, media_type=content_type)
