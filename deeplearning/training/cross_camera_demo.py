"""입구 홈캠 신원을 교실 카메라 ByteTrack에 인계하는 v2 실험 실행기."""

from __future__ import annotations

import ctypes
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np


def find_project_root() -> Path:
    start = Path.cwd().resolve()
    for candidate in (start, *start.parents):
        if (candidate / "deeplearning").is_dir() and (candidate / "webapps").is_dir():
            return candidate
    raise RuntimeError("smart_office_monitoring 저장소 안에서 실행하세요.")


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (item.strip() for item in line.split("=", 1))
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


PROJECT_ROOT = find_project_root()
for _env_path in (
    PROJECT_ROOT / "deeplearning/training/.env.face",
    PROJECT_ROOT / "webapps/fastapi/.env",
    PROJECT_ROOT / "webapps/fastapi/.env.local",
    PROJECT_ROOT / "deeplearning/training/.env",
    PROJECT_ROOT / "deeplearning/training/.env.local",
):
    load_env_file(_env_path)

from deeplearning.cross_camera_tracking import (  # noqa: E402
    CrossCameraCalibration,
    CrossCameraTracker,
    IdentityPayload,
    TrackObservation,
    point_in_polygon,
)
from deeplearning.face_identity import (  # noqa: E402
    FaceGallery,
    FaceIdentityEngine,
    GalleryEntry,
    IdentityThresholds,
)
from deeplearning.homecam_tracking import (  # noqa: E402
    PersonTrack,
    PersonTrackIdentityStore,
    TrackIdentity,
    TrackIdentityStatus,
    associate_faces_to_people,
)
from deeplearning.person_reid import (  # noqa: E402
    PersonReIdEngine,
    TrackFeatureStore,
)


def _as_bool(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class CameraConfig:
    camera_id: str
    source_type: str
    rtsp_url: str
    camera_index: int
    file_path: str = ""

    def source(self) -> str | int:
        if self.source_type == "rtsp":
            if not self.rtsp_url.startswith("rtsp://"):
                raise ValueError(f"{self.camera_id} RTSP 주소가 필요합니다.")
            return self.rtsp_url
        if self.source_type == "webcam":
            return self.camera_index
        if self.source_type == "file":
            # 실제 카메라 2대 없이 녹화된 영상으로 V2 파이프라인을 검증할 때 쓴다.
            # LatestFrameReader가 끝에 도달하면 처음으로 되감아 계속 재생한다.
            if not self.file_path:
                raise ValueError(f"{self.camera_id} 영상 파일 경로가 필요합니다.")
            if not Path(self.file_path).is_file():
                raise FileNotFoundError(self.file_path)
            return self.file_path
        raise ValueError(f"{self.camera_id} source는 rtsp, webcam 또는 file이어야 합니다.")


def load_camera_configs() -> tuple[CameraConfig, CameraConfig]:
    entry = CameraConfig(
        "entry",
        os.environ.get("ENTRY_CAMERA_SOURCE", os.environ.get("HOME_CAM_SOURCE", "rtsp")).lower(),
        os.environ.get("ENTRY_CAMERA_RTSP_URL")
        or os.environ.get("HOME_CAM_RTSP_URL", ""),
        int(os.environ.get("ENTRY_CAMERA_INDEX", os.environ.get("HOME_CAM_CAMERA_INDEX", "0"))),
        os.environ.get("ENTRY_CAMERA_FILE_PATH", ""),
    )
    classroom = CameraConfig(
        "classroom",
        os.environ.get("CLASSROOM_CAMERA_SOURCE", "webcam").lower(),
        os.environ.get("CLASSROOM_CAMERA_RTSP_URL")
        or os.environ.get("FACE_RTSP_URL", ""),
        int(os.environ.get("CLASSROOM_CAMERA_INDEX", "0")),
        os.environ.get("CLASSROOM_CAMERA_FILE_PATH", ""),
    )
    return entry, classroom


class LatestFrameReader:
    def __init__(self, config: CameraConfig) -> None:
        source = config.source()
        backend = (
            cv2.CAP_FFMPEG
            if config.source_type == "rtsp"
            else cv2.CAP_ANY
            if config.source_type == "file"
            else (cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY)
        )
        self._capture = cv2.VideoCapture(source, backend)
        if config.source_type == "rtsp":
            self._capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not self._capture.isOpened():
            self._capture.release()
            raise RuntimeError(f"{config.camera_id} 카메라 입력을 열지 못했습니다.")
        self._loop_file = config.source_type == "file"
        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._captured_at: float | None = None
        self._stopped = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stopped:
            ok, frame = self._capture.read()
            if not ok:
                if self._loop_file:
                    # 실제 카메라 2대 없이 녹화 영상으로 테스트할 때, 끝에 도달하면
                    # 처음으로 되감아 계속 재생한다(실시간 스트림처럼 동작).
                    self._capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                time.sleep(0.02)
                continue
            with self._lock:
                self._frame = frame
                self._captured_at = time.perf_counter()

    def read(self) -> tuple[np.ndarray | None, float | None]:
        with self._lock:
            if self._frame is None:
                return None, None
            return self._frame.copy(), self._captured_at

    def close(self) -> None:
        self._stopped = True
        self._capture.release()
        self._thread.join(timeout=2.0)


def wait_for_frame(reader: LatestFrameReader, label: str) -> np.ndarray:
    deadline = time.perf_counter() + 15.0
    while time.perf_counter() < deadline:
        frame, _ = reader.read()
        if frame is not None:
            return frame
        time.sleep(0.05)
    raise RuntimeError(f"{label} 카메라에서 15초 안에 프레임을 받지 못했습니다.")


def _collect_points(
    frame: np.ndarray,
    *,
    title: str,
    minimum: int,
    exact: int | None = None,
) -> tuple[tuple[float, float], ...]:
    points: list[tuple[int, int]] = []

    def on_mouse(event: int, x: int, y: int, flags: int, param: Any) -> None:
        if event == cv2.EVENT_LBUTTONDOWN and (exact is None or len(points) < exact):
            points.append((x, y))

    cv2.namedWindow(title, cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO)
    cv2.setMouseCallback(title, on_mouse)
    try:
        while True:
            display = frame.copy()
            for index, point in enumerate(points):
                cv2.circle(display, point, 5, (0, 255, 255), -1)
                cv2.putText(display, str(index + 1), point, cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
            if len(points) >= 2:
                cv2.polylines(display, [np.asarray(points)], False, (0, 255, 255), 2)
            guide = "left click: point | r: reset | Enter: finish | Esc: cancel"
            cv2.putText(display, guide, (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
            cv2.imshow(title, display)
            key = cv2.waitKeyEx(20)
            if key in (13, 10) and len(points) >= minimum and (exact is None or len(points) == exact):
                break
            if key in (ord("r"), ord("R")):
                points.clear()
            if key == 27:
                raise RuntimeError("카메라 보정을 취소했습니다.")
    finally:
        cv2.destroyWindow(title)
    height, width = frame.shape[:2]
    return tuple((x / width, y / height) for x, y in points)


def calibrate(
    entry_frame: np.ndarray,
    classroom_frame: np.ndarray,
) -> CrossCameraCalibration:
    print("1/4 입구 카메라에서 두 카메라가 함께 보는 겹침 구역을 시계방향으로 클릭하세요.")
    entry_polygon = _collect_points(entry_frame, title="1 entry overlap polygon", minimum=3)
    print("2/4 교실 카메라에서 같은 실제 겹침 구역을 시계방향으로 클릭하세요.")
    classroom_polygon = _collect_points(classroom_frame, title="2 classroom overlap polygon", minimum=3)
    print("3/4 입구 카메라에서 바닥 대응점 4개를 순서대로 클릭하세요.")
    entry_points = _collect_points(
        entry_frame,
        title="3 entry floor points",
        minimum=4,
    )
    print(
        f"4/4 교실 카메라에서 같은 바닥점 {len(entry_points)}개를 "
        "같은 순서로 클릭하세요."
    )
    classroom_points = _collect_points(
        classroom_frame,
        title="4 classroom floor points",
        minimum=len(entry_points),
        exact=len(entry_points),
    )
    return CrossCameraCalibration(
        entry_resolution=(entry_frame.shape[1], entry_frame.shape[0]),
        classroom_resolution=(classroom_frame.shape[1], classroom_frame.shape[0]),
        entry_overlap_polygon=entry_polygon,
        classroom_overlap_polygon=classroom_polygon,
        entry_correspondence_points=entry_points,
        classroom_correspondence_points=classroom_points,
    )


def _prepare_cuda_dlls() -> None:
    if os.name != "nt":
        return
    import torch

    torch_dll_dir = Path(torch.__file__).resolve().parent / "lib"
    cudnn_dll = torch_dll_dir / "cudnn64_9.dll"
    if not cudnn_dll.is_file():
        raise FileNotFoundError(cudnn_dll)
    os.environ["PATH"] = f"{torch_dll_dir}{os.pathsep}{os.environ.get('PATH', '')}"
    global _torch_dll_dir_handle, _cudnn_handle
    _torch_dll_dir_handle = os.add_dll_directory(str(torch_dll_dir))
    _cudnn_handle = ctypes.WinDLL(str(cudnn_dll))


def build_face_engine() -> tuple[FaceIdentityEngine, dict[str, str]]:
    _prepare_cuda_dlls()
    import onnxruntime as ort
    from insightface.model_zoo import get_model
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError

    model_root = PROJECT_ROOT / "deeplearning/.models"
    detector_path = Path(os.environ.get("FACE_DETECTION_MODEL_PATH") or model_root / "scrfd/scrfd_10g_bnkps.onnx").resolve()
    recognizer_path = Path(os.environ.get("FACE_RECOGNITION_MODEL_PATH") or model_root / "buffalo_l/w600k_r50.onnx").resolve()
    threshold_path = Path(os.environ["OPEN_SET_THRESHOLD_FILE"]).resolve()
    for path in (detector_path, recognizer_path, threshold_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    mongodb_uri = os.environ.get("MONGODB_URI") or os.environ.get("DATABASE_URL", "")
    mongodb_database = os.environ.get("MONGODB_DATABASE") or os.environ.get("DATABASE_NAME", "")
    collection_name = os.environ.get("FACE_EMBEDDING_COLLECTION", "face_embeddings")
    expected_metadata = ("arcface", "insightface-buffalo_l-w600k_r50-v0.7", "insightface-norm-crop-112-v1")
    names: dict[str, str] = {}
    entries: list[GalleryEntry] = []
    client = MongoClient(mongodb_uri, serverSelectionTimeoutMS=10_000, connectTimeoutMS=10_000)
    try:
        client.admin.command("ping")
        projection = {"_id": 0, "student_id": 1, "student_name": 1, "vector": 1, "dimension": 1, "normalized": 1, "model_name": 1, "model_version": 1, "preprocessing_version": 1}
        for document in client[mongodb_database][collection_name].find({}, projection):
            student_id = document.get("student_id")
            metadata = (document.get("model_name"), document.get("model_version"), document.get("preprocessing_version"))
            if not isinstance(student_id, str) or not student_id or document.get("dimension") != 512 or document.get("normalized") is not True or metadata != expected_metadata:
                raise RuntimeError(f"{student_id!r} 얼굴 벡터 metadata가 현재 ArcFace와 다릅니다.")
            entries.append(GalleryEntry(student_id, np.asarray(document.get("vector"), dtype=np.float32)))
            names[student_id] = str(document.get("student_name") or student_id)
    except PyMongoError as exc:
        raise RuntimeError("MongoDB 연결/조회에 실패했습니다.") from exc
    finally:
        client.close()
    if not entries:
        raise RuntimeError("등록 얼굴 갤러리가 비어 있습니다.")
    providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    if "CUDAExecutionProvider" not in ort.get_available_providers():
        providers = ["CPUExecutionProvider"]
    detector = get_model(str(detector_path), providers=providers)
    input_size = int(os.environ.get("FACE_DETECTION_INPUT_SIZE", "1280"))
    detector.prepare(ctx_id=0 if providers[0].startswith("CUDA") else -1, input_size=(input_size, input_size), det_thresh=float(os.environ.get("FACE_DETECTION_THRESHOLD", "0.4")))
    recognizer = get_model(str(recognizer_path), providers=providers)
    recognizer.prepare(ctx_id=0 if providers[0].startswith("CUDA") else -1)
    threshold_data = json.loads(threshold_path.read_text(encoding="utf-8"))
    engine = FaceIdentityEngine(
        detector=detector,
        recognizer=recognizer,
        gallery=FaceGallery.from_entries(entries),
        thresholds=IdentityThresholds(float(threshold_data["similarity_threshold"]), float(threshold_data["margin_threshold"])),
        detection_threshold=float(os.environ.get("FACE_DETECTION_THRESHOLD", "0.4")),
        identity_min_detection_confidence=float(os.environ.get("FACE_IDENTITY_MIN_DETECTION_CONFIDENCE", "0.6")),
        minimum_face_size=int(os.environ.get("FACE_MINIMUM_SIZE", "40")),
        preferred_face_size=int(os.environ.get("FACE_PREFERRED_SIZE", "112")),
        minimum_blur_score=float(os.environ.get("FACE_MINIMUM_BLUR_SCORE", "20")),
        preferred_blur_score=float(os.environ.get("FACE_PREFERRED_BLUR_SCORE", "100")),
        uncertain_quality_threshold=float(os.environ.get("FACE_UNCERTAIN_QUALITY_THRESHOLD", "0.45")),
        use_flip_tta=_as_bool("FACE_USE_FLIP_TTA", "true"),
        tta_similarity_band=float(os.environ.get("FACE_TTA_SIMILARITY_BAND", "0.08")),
        tta_margin_band=float(os.environ.get("FACE_TTA_MARGIN_BAND", "0.06")),
    )
    detector.detect(np.zeros((input_size, input_size, 3), dtype=np.uint8), max_num=0)
    recognizer.get_feat(np.zeros((112, 112, 3), dtype=np.uint8))
    return engine, names


def extract_people(result: Any) -> tuple[PersonTrack, ...]:
    boxes = result.boxes
    if boxes is None or boxes.id is None:
        return ()
    xyxy = boxes.xyxy.detach().cpu().numpy()
    ids = boxes.id.detach().cpu().numpy().astype(int)
    confidences = boxes.conf.detach().cpu().numpy()
    return tuple(
        PersonTrack(
            int(track_id),
            tuple(int(value) for value in bbox),
            float(confidence),
        )
        for bbox, track_id, confidence in zip(
            xyxy,
            ids,
            confidences,
            strict=True,
        )
    )


def foot_point(track: PersonTrack, frame: np.ndarray) -> tuple[float, float]:
    height, width = frame.shape[:2]
    return ((track.bbox[0] + track.bbox[2]) / (2.0 * width), track.bbox[3] / height)


def _draw_polygon(frame: np.ndarray, polygon: tuple[tuple[float, float], ...]) -> None:
    height, width = frame.shape[:2]
    points = np.asarray([(round(x * width), round(y * height)) for x, y in polygon], dtype=np.int32)
    cv2.polylines(frame, [points], True, (255, 0, 255), 2)


def _status_style(status: TrackIdentityStatus) -> tuple[str, tuple[int, int, int]]:
    return {
        TrackIdentityStatus.REGISTERED: ("registered", (0, 200, 0)),
        TrackIdentityStatus.UNKNOWN: ("unknown", (0, 0, 255)),
        TrackIdentityStatus.UNCERTAIN: ("review", (0, 200, 255)),
    }[status]


def _resize_height(frame: np.ndarray, target_height: int = 540) -> np.ndarray:
    scale = target_height / frame.shape[0]
    return cv2.resize(frame, (max(1, round(frame.shape[1] * scale)), target_height), interpolation=cv2.INTER_AREA)


def run() -> None:
    from ultralytics import YOLO

    entry_config, classroom_config = load_camera_configs()
    entry_reader = LatestFrameReader(entry_config)
    try:
        classroom_reader = LatestFrameReader(classroom_config)
    except Exception:
        entry_reader.close()
        raise
    calibration_path = Path(os.environ.get("CROSS_CAMERA_CALIBRATION_FILE") or PROJECT_ROOT / "deeplearning/training/runs/cross_camera_tracking/calibration.json")
    diagnostic_dir = Path(os.environ.get("CROSS_CAMERA_DIAGNOSTIC_OUTPUT_DIR") or PROJECT_ROOT / "deeplearning/training/runs/cross_camera_tracking")
    try:
        entry_frame = wait_for_frame(entry_reader, "entry")
        classroom_frame = wait_for_frame(classroom_reader, "classroom")
        if calibration_path.is_file() and not _as_bool("CROSS_CAMERA_RECALIBRATE"):
            calibration = CrossCameraCalibration.load(calibration_path)
        else:
            calibration = calibrate(entry_frame, classroom_frame)
            calibration.save(calibration_path)
            print(f"카메라 보정 저장: {calibration_path}")

        face_engine, student_names = build_face_engine()
        model_path = os.environ.get("HOME_CAM_PERSON_MODEL_PATH", "yolo11m.pt")
        entry_model, classroom_model = YOLO(model_path), YOLO(model_path)
        device = os.environ.get("DEVICE", "cuda")
        entry_model.to(device)
        classroom_model.to(device)
        reid_path = Path(os.environ.get("PERSON_REID_MODEL_PATH") or PROJECT_ROOT / "deeplearning/.models/person_reid/osnet_ain_x1_0_msmt17.onnx")
        reid_engine = PersonReIdEngine(reid_path)
        feature_store = TrackFeatureStore(history_size=int(os.environ.get("PERSON_REID_HISTORY_SIZE", "8")))
        identity_store = PersonTrackIdentityStore(
            history_size=int(os.environ.get("HOME_CAM_IDENTITY_HISTORY_SIZE", "12")),
            minimum_observations=int(os.environ.get("HOME_CAM_IDENTITY_MINIMUM_OBSERVATIONS", "4")),
            stale_frames=int(os.environ.get("HOME_CAM_TRACK_STALE_FRAMES", "30")),
        )
        cross_tracker = CrossCameraTracker(
            calibration,
            appearance_weight=float(os.environ.get("CROSS_CAMERA_APPEARANCE_WEIGHT", "0.60")),
            geometry_weight=float(os.environ.get("CROSS_CAMERA_GEOMETRY_WEIGHT", "0.25")),
            time_weight=float(os.environ.get("CROSS_CAMERA_TIME_WEIGHT", "0.15")),
            minimum_score=float(os.environ.get("CROSS_CAMERA_MINIMUM_SCORE", "0.70")),
            minimum_margin=float(os.environ.get("CROSS_CAMERA_MINIMUM_MARGIN", "0.08")),
            maximum_time_difference=float(os.environ.get("CROSS_CAMERA_MAXIMUM_TIME_DIFFERENCE", "0.50")),
            maximum_geometry_distance=float(os.environ.get("CROSS_CAMERA_MAXIMUM_GEOMETRY_DISTANCE", "0.20")),
            stale_seconds=float(os.environ.get("CROSS_CAMERA_STALE_SECONDS", "2.0")),
        )
        tracker_config = os.environ.get("HOME_CAM_TRACKER_CONFIG", "bytetrack.yaml")
        confidence = float(os.environ.get("HOME_CAM_PERSON_CONFIDENCE", "0.25"))
        face_interval = int(os.environ.get("FACE_RECOGNITION_INTERVAL", "6"))
        reid_interval = int(os.environ.get("PERSON_REID_INTERVAL", "3"))
        frame_index = 0
        last_entry_time: float | None = None
        last_classroom_time: float | None = None
        frame_durations: list[float] = []
        entry_latencies: list[float] = []
        classroom_latencies: list[float] = []
        peak_entry_tracks = peak_classroom_tracks = 0
        window = "cross camera tracking v2"
        cv2.namedWindow(window, cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO)
        while True:
            started = time.perf_counter()
            entry_frame, entry_time = entry_reader.read()
            classroom_frame, classroom_time = classroom_reader.read()
            if entry_frame is None or classroom_frame is None or entry_time is None or classroom_time is None:
                time.sleep(0.01)
                continue
            if entry_time == last_entry_time or classroom_time == last_classroom_time:
                cross_tracker.expire(now=time.perf_counter())
                time.sleep(0.005)
                continue
            last_entry_time = entry_time
            last_classroom_time = classroom_time
            frame_index += 1
            entry_result = entry_model.track(entry_frame, persist=True, tracker=tracker_config, classes=[0], conf=confidence, device=device, verbose=False)[0]
            classroom_result = classroom_model.track(classroom_frame, persist=True, tracker=tracker_config, classes=[0], conf=confidence, device=device, verbose=False)[0]
            entry_people = extract_people(entry_result)
            classroom_people = extract_people(classroom_result)
            peak_entry_tracks = max(peak_entry_tracks, len(entry_people))
            peak_classroom_tracks = max(peak_classroom_tracks, len(classroom_people))

            if frame_index == 1 or frame_index % face_interval == 0:
                faces = face_engine.identify(entry_frame, extract_embeddings=True)
                face_associations = associate_faces_to_people(entry_people, faces, minimum_face_coverage=float(os.environ.get("HOME_CAM_FACE_COVERAGE_THRESHOLD", "0.80")))
            else:
                faces, face_associations = (), ()
            identities = identity_store.update(entry_people, faces, face_associations, now=entry_time)
            identity_by_track: dict[int, TrackIdentity] = {item.track_id: item for item in identities}

            active_keys = {("entry", item.track_id) for item in entry_people} | {("classroom", item.track_id) for item in classroom_people}
            if frame_index == 1 or frame_index % reid_interval == 0:
                for person in entry_people:
                    if point_in_polygon(foot_point(person, entry_frame), calibration.entry_overlap_polygon):
                        feature_store.update("entry", person, entry_frame, reid_engine)
                for person in classroom_people:
                    if point_in_polygon(foot_point(person, classroom_frame), calibration.classroom_overlap_polygon):
                        feature_store.update("classroom", person, classroom_frame, reid_engine)
            feature_store.retain(active_keys)

            entry_observations: list[TrackObservation] = []
            for person in entry_people:
                identity = identity_by_track[person.track_id]
                observation = TrackObservation("entry", person.track_id, foot_point(person, entry_frame), entry_time, feature_store.get("entry", person.track_id))
                cross_tracker.register_entry(observation, IdentityPayload(identity.status, identity.student_id))
                entry_observations.append(observation)
            classroom_observations = [TrackObservation("classroom", person.track_id, foot_point(person, classroom_frame), classroom_time, feature_store.get("classroom", person.track_id)) for person in classroom_people]
            cross_tracker.match(entry_observations, classroom_observations)

            for person in entry_people:
                view = cross_tracker.lookup(("entry", person.track_id), now=entry_time)
                if view is None:
                    continue
                label, color = _status_style(view.identity.status)
                name = student_names.get(view.identity.student_id, view.identity.student_id or label)
                left, top, right, bottom = person.bbox
                cv2.rectangle(entry_frame, (left, top), (right, bottom), color, 2)
                cv2.putText(entry_frame, f"E{person.track_id} {view.global_track_id} {name}", (left, max(20, top - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
            for person in classroom_people:
                view = cross_tracker.lookup(("classroom", person.track_id), now=classroom_time)
                left, top, right, bottom = person.bbox
                if view is None:
                    color, text = (160, 160, 160), f"C{person.track_id} unmapped"
                else:
                    _, color = _status_style(view.identity.status)
                    name = student_names.get(view.identity.student_id, view.identity.student_id or "review")
                    text = f"C{person.track_id} {view.global_track_id} {name}"
                cv2.rectangle(classroom_frame, (left, top), (right, bottom), color, 2)
                cv2.putText(classroom_frame, text, (left, max(20, top - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
            _draw_polygon(entry_frame, calibration.entry_overlap_polygon)
            _draw_polygon(classroom_frame, calibration.classroom_overlap_polygon)
            cv2.putText(entry_frame, "ENTRY: face identity + ByteTrack", (15, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
            cv2.putText(classroom_frame, "CLASSROOM: ByteTrack only", (15, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
            combined = np.hstack((_resize_height(entry_frame), _resize_height(classroom_frame)))
            cv2.imshow(window, combined)

            now = max(entry_time, classroom_time)
            cross_tracker.expire(now=now)
            duration = time.perf_counter() - started
            frame_durations.append(duration)
            entry_latencies.append(max(0.0, started - entry_time))
            classroom_latencies.append(max(0.0, started - classroom_time))
            if cv2.waitKeyEx(1) & 0xFF == ord("q"):
                break
    finally:
        entry_reader.close()
        classroom_reader.close()
        cv2.destroyAllWindows()
        if "cross_tracker" in locals():
            diagnostic_dir.mkdir(parents=True, exist_ok=True)
            average_duration = sum(frame_durations) / len(frame_durations) if frame_durations else None
            report = {
                "cross_camera": cross_tracker.snapshot(),
                "identity_switch_count": identity_store.identity_switch_count,
                "entry": {"peak_tracks": peak_entry_tracks, "average_capture_latency_ms": 1000 * sum(entry_latencies) / len(entry_latencies) if entry_latencies else None},
                "classroom": {"peak_tracks": peak_classroom_tracks, "average_capture_latency_ms": 1000 * sum(classroom_latencies) / len(classroom_latencies) if classroom_latencies else None},
                "average_fps": 1.0 / average_duration if average_duration else None,
                "privacy": "영상·얼굴 크롭·ArcFace/OSNet embedding은 저장하지 않음",
            }
            output_path = diagnostic_dir / f"cross-camera-v2-{time.strftime('%Y%m%d-%H%M%S')}.json"
            output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"교차 카메라 진단 저장: {output_path}")
