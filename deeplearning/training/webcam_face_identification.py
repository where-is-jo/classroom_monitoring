"""노트북 웹캠으로 운영 AdaFace 얼굴 식별 경로를 실시간 검증한다.

프레임이나 얼굴 이미지는 디스크에 저장하지 않는다. 이 도구는 카메라 입력만 로컬
웹캠으로 대체하며, 얼굴 검출·임베딩·MongoDB 갤러리 비교는 deeplearning 서버의
``POST /internal/face-identifications``를 그대로 사용한다.
"""

from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass
from typing import Any

import cv2
import requests


@dataclass(frozen=True)
class FaceObservation:
    bbox: tuple[int, int, int, int]
    status: str
    student_id: str | None
    similarity: float | None
    observation_count: int
    display_name: str | None = None


class AdaFaceIdentificationClient:
    def __init__(
        self,
        base_url: str,
        *,
        camera_id: str,
        timeout_seconds: float = 5.0,
        jpeg_quality: int = 95,
        session: requests.Session | None = None,
    ) -> None:
        if not camera_id.strip() or len(camera_id) > 128:
            raise ValueError("카메라 ID가 올바르지 않습니다.")
        if timeout_seconds <= 0:
            raise ValueError("요청 제한 시간은 0초보다 커야 합니다.")
        if not 1 <= jpeg_quality <= 100:
            raise ValueError("JPEG 품질은 1부터 100 사이여야 합니다.")
        self._base_url = base_url.rstrip("/")
        self._camera_id = camera_id
        self._timeout = timeout_seconds
        self._jpeg_quality = jpeg_quality
        self._session = session or requests.Session()

    def ensure_adaface_ready(self) -> dict[str, Any]:
        response = self._session.get(
            f"{self._base_url}/health/ready", timeout=self._timeout
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("얼굴 식별 서버 readiness 응답이 올바르지 않습니다.")
        if (
            payload.get("status") != "ready"
            or payload.get("face_identification") != "ready"
            or payload.get("active_face_model") != "adaface"
            or payload.get("missing_gallery_entries") != "0"
        ):
            raise RuntimeError(
                "AdaFace 모델과 face_embeddings_adaface 갤러리가 준비되지 않았습니다."
            )
        return payload

    def identify(self, frame: Any) -> tuple[FaceObservation, ...]:
        encoded, buffer = cv2.imencode(
            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality]
        )
        if not encoded:
            raise RuntimeError("웹캠 프레임을 JPEG로 변환하지 못했습니다.")
        response = self._session.post(
            f"{self._base_url}/internal/face-identifications",
            data=bytes(buffer.tobytes()),
            headers={
                "Content-Type": "image/jpeg",
                "X-Camera-ID": self._camera_id,
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        return parse_observations(response.json())


class LocalAdaFaceIdentificationClient:
    """운영 객체를 그대로 조립해 로컬 CUDA에서 AdaFace를 실행한다."""

    def __init__(
        self,
        *,
        detector_path: str,
        recognizer_path: str,
        database_url: str,
        database_name: str,
        similarity_threshold: float,
        margin_threshold: float,
        track_similarity_threshold: float,
    ) -> None:
        import onnxruntime as ort
        from insightface.model_zoo import get_model

        from deeplearning.face_identification import (
            FaceIdentificationConfig,
            FaceIdentificationRuntime,
            MongoFaceGalleryLoader,
        )
        from deeplearning.face_recognizer import (
            build_face_recognizer,
            load_face_recognizer_config,
        )

        available = ort.get_available_providers()
        provider_candidates = (
            (
                ["CUDAExecutionProvider", "CPUExecutionProvider"],
                ["CPUExecutionProvider"],
            )
            if "CUDAExecutionProvider" in available
            else (["CPUExecutionProvider"],)
        )
        environment = {
            "FACE_RECOGNIZER": "adaface",
            "FACE_RECOGNITION_MODEL_PATH": recognizer_path,
            "FACE_RECOGNITION_MODEL_VERSION": (
                "cvlface-adaface-ir50-webface4m-fe7718c6"
            ),
            "FACE_EMBEDDING_COLLECTION": "face_embeddings_adaface",
        }
        recognizer_config = load_face_recognizer_config(environment)
        last_error: Exception | None = None
        for providers in provider_candidates:
            try:
                recognizer = build_face_recognizer(
                    recognizer_config, providers=providers
                )
                detector = get_model(detector_path, providers=providers)
                detector.prepare(
                    ctx_id=0 if providers[0].startswith("CUDA") else -1,
                    input_size=(640, 640),
                    det_thresh=0.5,
                )
                break
            except Exception as error:
                last_error = error
        else:
            raise RuntimeError(
                "AdaFace 로컬 추론 provider를 준비하지 못했습니다."
            ) from last_error
        gallery_loader = MongoFaceGalleryLoader(
            database_url=database_url,
            database_name=database_name,
            collection_name=recognizer_config.collection_name,
            expected_metadata=recognizer_config.metadata,
        )
        self._runtime = FaceIdentificationRuntime(
            detector=detector,
            recognizer=recognizer,
            gallery_loader=gallery_loader,
            config=FaceIdentificationConfig(
                similarity_threshold=similarity_threshold,
                margin_threshold=margin_threshold,
                track_similarity_threshold=track_similarity_threshold,
                gallery_refresh_seconds=30,
                detection_threshold=0.5,
                identity_min_detection_confidence=0.6,
                minimum_face_size=30,
                preferred_face_size=112,
                minimum_blur_score=20,
                preferred_blur_score=100,
                uncertain_quality_threshold=0.45,
                use_flip_tta=True,
                tta_similarity_band=0.08,
                tta_margin_band=0.06,
                tracker_history_size=12,
                tracker_minimum_observations=3,
                tracker_stale_frames=30,
            ),
        )
        self._camera_id = "notebook-webcam"
        self.providers = tuple(providers)
        from pymongo import MongoClient

        mongo_client = MongoClient(database_url, serverSelectionTimeoutMS=5_000)
        try:
            self._student_names = {
                str(value.get("student_id")): str(value.get("student_name"))
                for value in mongo_client[database_name][
                    "face_embeddings_adaface"
                ].find({}, {"student_id": 1, "student_name": 1})
                if value.get("student_id") and value.get("student_name")
            }
        finally:
            mongo_client.close()

    def ensure_adaface_ready(self) -> dict[str, Any]:
        ready = self._runtime.ensure_ready()
        return {
            "status": "ready",
            "face_identification": "ready",
            "active_face_model": "adaface",
            "face_model_version": "cvlface-adaface-ir50-webface4m-fe7718c6",
            "gallery_entries": str(ready.gallery_entries),
            "missing_gallery_entries": str(ready.missing_gallery_entries),
            "providers": list(self.providers),
        }

    def identify(self, frame: Any) -> tuple[FaceObservation, ...]:
        identities = self._runtime.identify(
            camera_id=self._camera_id,
            image_bgr=frame,
        )
        return tuple(
            FaceObservation(
                bbox=value.bbox,
                status=value.status.name,
                student_id=value.student_id,
                similarity=value.similarity,
                observation_count=value.observation_count,
                display_name=(
                    self._student_names.get(value.student_id)
                    if value.student_id is not None
                    else None
                ),
            )
            for value in identities
        )


def parse_observations(payload: Any) -> tuple[FaceObservation, ...]:
    if not isinstance(payload, dict) or not isinstance(
        payload.get("observations"), list
    ):
        raise RuntimeError("얼굴 식별 서버 응답 형식이 올바르지 않습니다.")
    parsed: list[FaceObservation] = []
    for value in payload["observations"]:
        if not isinstance(value, dict):
            raise RuntimeError("얼굴 관측 응답 형식이 올바르지 않습니다.")
        bbox = value.get("face_bbox")
        status = value.get("identity_status")
        if (
            not isinstance(bbox, list)
            or len(bbox) != 4
            or not all(isinstance(item, (int, float)) for item in bbox)
            or status not in {"REGISTERED", "UNKNOWN", "UNCERTAIN"}
        ):
            raise RuntimeError("얼굴 관측 필드가 올바르지 않습니다.")
        student_id = value.get("student_id")
        similarity = value.get("similarity")
        observation_count = value.get("observation_count", 0)
        parsed.append(
            FaceObservation(
                bbox=tuple(int(item) for item in bbox),  # type: ignore[arg-type]
                status=status,
                student_id=student_id if isinstance(student_id, str) else None,
                similarity=(
                    float(similarity)
                    if isinstance(similarity, (int, float))
                    and not isinstance(similarity, bool)
                    else None
                ),
                observation_count=(
                    observation_count if isinstance(observation_count, int) else 0
                ),
            )
        )
    return tuple(parsed)


def _draw_observations(
    frame: Any,
    observations: tuple[FaceObservation, ...],
    *,
    latency_seconds: float | None,
) -> None:
    colors = {
        "REGISTERED": (40, 200, 40),
        "UNCERTAIN": (0, 190, 255),
        "UNKNOWN": (40, 40, 230),
    }
    for observation in observations:
        left, top, right, bottom = observation.bbox
        color = colors[observation.status]
        cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
        label = observation.status
        if observation.display_name:
            label += f" {observation.display_name}"
        elif observation.student_id:
            label += f" {observation.student_id}"
        if observation.similarity is not None:
            label += f" sim={observation.similarity:.3f}"
        label += f" votes={observation.observation_count}"
        cv2.putText(
            frame,
            label,
            (left, max(20, top - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            color,
            2,
        )
    latency = "-" if latency_seconds is None else f"{latency_seconds * 1000:.0f}ms"
    cv2.putText(
        frame,
        f"AdaFace / latency={latency} / q: quit",
        (12, 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (255, 255, 255),
        2,
    )


def _open_camera(
    camera_index: int,
    *,
    width: int,
    height: int,
    rtsp_url: str | None = None,
) -> Any:
    capture = (
        cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
        if rtsp_url
        else cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
    )
    if not capture.isOpened():
        capture.release()
        source = "RTSP 스트림" if rtsp_url else f"노트북 카메라 {camera_index}번"
        raise RuntimeError(f"{source}을 열 수 없습니다.")
    if rtsp_url:
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    else:
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        capture.set(cv2.CAP_PROP_FPS, 30)
    actual_width = round(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_height = round(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    actual_fps = capture.get(cv2.CAP_PROP_FPS)
    print(
        f"입력 {'RTSP' if rtsp_url else camera_index}: {actual_width}x{actual_height}, "
        f"요청 FPS=30, 장치 보고 FPS={actual_fps:.1f}"
    )
    return capture


def run_camera_preview(
    *, camera_index: int, width: int, height: int, rtsp_url: str | None = None
) -> None:
    """서버·DB 없이 내일 사용할 카메라 번호와 영상 입력만 확인한다."""

    capture = _open_camera(camera_index, width=width, height=height, rtsp_url=rtsp_url)
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                raise RuntimeError("노트북 카메라 프레임을 읽지 못했습니다.")
            cv2.putText(
                frame,
                f"source={'RTSP' if rtsp_url else camera_index} / preview only / q: quit",
                (12, 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (255, 255, 255),
                2,
            )
            cv2.imshow("Notebook webcam preview", frame)
            if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                break
    finally:
        capture.release()
        cv2.destroyAllWindows()


def run_webcam(
    *,
    client: Any,
    camera_index: int,
    inference_interval: int,
    width: int,
    height: int,
    rtsp_url: str | None = None,
) -> None:
    if inference_interval < 1:
        raise ValueError("추론 간격은 1 이상이어야 합니다.")
    ready = client.ensure_adaface_ready()
    print(
        "AdaFace 준비 완료: "
        f"모델={ready.get('face_model_version')}, "
        f"갤러리={ready.get('gallery_entries')}"
    )

    capture = _open_camera(camera_index, width=width, height=height, rtsp_url=rtsp_url)

    frame_index = 0
    observations: tuple[FaceObservation, ...] = ()
    latency_seconds: float | None = None
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                raise RuntimeError("노트북 카메라 프레임을 읽지 못했습니다.")
            if frame_index % inference_interval == 0:
                started_at = time.perf_counter()
                try:
                    observations = client.identify(frame)
                    latency_seconds = time.perf_counter() - started_at
                except requests.RequestException as error:
                    latency_seconds = None
                    print(f"얼굴 식별 요청 실패: {error}")
            frame_index += 1
            _draw_observations(frame, observations, latency_seconds=latency_seconds)
            cv2.imshow("AdaFace notebook webcam verification", frame)
            if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                break
    finally:
        capture.release()
        cv2.destroyAllWindows()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8100")
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument(
        "--rtsp-url",
        default="",
        help="지정하면 노트북 카메라 대신 입구 카메라 RTSP를 읽습니다.",
    )
    parser.add_argument("--camera-id", default="notebook-webcam")
    parser.add_argument(
        "--inference-interval",
        type=int,
        default=1,
        help="몇 프레임마다 얼굴 식별을 요청할지 지정합니다.",
    )
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument(
        "--local",
        action="store_true",
        help="원격 서버 대신 로컬 CUDA·MongoDB로 운영 AdaFace 객체를 실행합니다.",
    )
    parser.add_argument(
        "--detector-path",
        default="deeplearning/.models/scrfd/scrfd_10g_bnkps.onnx",
    )
    parser.add_argument(
        "--recognizer-path",
        default="deeplearning/.models/adaface/adaface_ir50_webface4m.onnx",
    )
    parser.add_argument("--database-url", default="")
    parser.add_argument("--database-name", default="classroom_monitoring")
    parser.add_argument("--similarity-threshold", type=float)
    parser.add_argument("--margin-threshold", type=float)
    parser.add_argument("--track-similarity-threshold", type=float)
    parser.add_argument(
        "--diagnostic-only",
        action="store_true",
        help="임계값 확정 전 얼굴 검출·유사도·속도만 확인하고 학생을 확정하지 않습니다.",
    )
    parser.add_argument(
        "--camera-check-only",
        action="store_true",
        help="서버에 연결하지 않고 카메라 화면만 확인합니다.",
    )
    args = parser.parse_args(argv)

    if args.camera_check_only:
        run_camera_preview(
            camera_index=args.camera_index,
            width=args.width,
            height=args.height,
            rtsp_url=args.rtsp_url or None,
        )
        return 0

    if args.local:
        from dotenv import load_dotenv

        load_dotenv("deeplearning/training/.env.face", override=False)
        database_url = args.database_url or os.environ.get("MONGODB_URI", "")
        if not database_url:
            parser.error("--local에는 --database-url 또는 MONGODB_URI가 필요합니다.")
        thresholds = (
            args.similarity_threshold,
            args.margin_threshold,
            args.track_similarity_threshold,
        )
        if args.diagnostic_only:
            thresholds = (1.0, 2.0, 1.0)
        elif any(value is None for value in thresholds):
            parser.error(
                "--local에는 AdaFace 평가값인 --similarity-threshold, "
                "--margin-threshold, --track-similarity-threshold가 필요합니다."
            )
        client: Any = LocalAdaFaceIdentificationClient(
            detector_path=args.detector_path,
            recognizer_path=args.recognizer_path,
            database_url=database_url,
            database_name=args.database_name,
            similarity_threshold=float(thresholds[0]),
            margin_threshold=float(thresholds[1]),
            track_similarity_threshold=float(thresholds[2]),
        )
    else:
        client = AdaFaceIdentificationClient(
            args.url,
            camera_id=args.camera_id,
            timeout_seconds=args.timeout_seconds,
        )
    run_webcam(
        client=client,
        camera_index=args.camera_index,
        inference_interval=args.inference_interval,
        width=args.width,
        height=args.height,
        rtsp_url=args.rtsp_url or None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
