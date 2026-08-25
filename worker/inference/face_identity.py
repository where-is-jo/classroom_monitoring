"""입구 카메라 프레임을 얼굴 관측 계약으로 처리하고 저장하는 실행 경계."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC
from typing import Any

import cv2
import requests

from shared.types import CapturedFrame

from .metrics import (
    FACE_IDENTIFICATION_DURATION_SECONDS,
    FACE_IDENTIFICATION_REQUESTS_TOTAL,
)
from .types import (
    EntryFaceObservation,
    EntryFaceObservationBatch,
    EntryIdentityProcessingStatus,
    EntryIdentityStatus,
)

logger = logging.getLogger(__name__)

IDENTIFICATION_PATH = "/internal/face-identifications"
ENTRY_IDENTITY_EVENTS_PATH = "/internal/entry-identity-events"


class FaceIdentificationError(RuntimeError):
    """얼굴 분석을 완료하지 못했을 때 처리 상태와 함께 발생한다."""

    processing_status: EntryIdentityProcessingStatus


class FaceAnalyzerUnavailableError(FaceIdentificationError):
    processing_status = EntryIdentityProcessingStatus.ANALYZER_UNAVAILABLE


class FaceIdentificationResponseError(FaceIdentificationError):
    processing_status = EntryIdentityProcessingStatus.INVALID_RESPONSE


class HttpFaceIdentifier:
    """모델 세부사항 없이 deeplearning의 얼굴 관측 API만 호출한다."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float,
        jpeg_quality: int,
        post: Callable[..., requests.Response] = requests.post,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("얼굴 식별 timeout은 0보다 커야 합니다.")
        if not 1 <= jpeg_quality <= 100:
            raise ValueError("얼굴 식별 JPEG 품질은 1과 100 사이여야 합니다.")
        self._url = base_url.rstrip("/") + IDENTIFICATION_PATH
        self._timeout_seconds = timeout_seconds
        self._jpeg_quality = jpeg_quality
        self._post = post

    def identify(self, captured: CapturedFrame) -> tuple[EntryFaceObservation, ...]:
        encoded, buffer = cv2.imencode(
            ".jpg",
            captured.frame,
            [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality],
        )
        if not encoded:
            raise FaceIdentificationResponseError(
                "얼굴 식별용 JPEG를 만들지 못했습니다."
            )

        started_at = time.perf_counter()
        try:
            try:
                response = self._post(
                    self._url,
                    data=bytes(buffer.tobytes()),
                    headers={
                        "Content-Type": "image/jpeg",
                        "X-Camera-ID": captured.camera_id,
                    },
                    timeout=self._timeout_seconds,
                )
                response.raise_for_status()
            except requests.RequestException as error:
                FACE_IDENTIFICATION_REQUESTS_TOTAL.labels(outcome="error").inc()
                raise FaceAnalyzerUnavailableError(
                    "얼굴 식별 서비스 호출에 실패했습니다."
                ) from error
            try:
                payload = response.json()
            except ValueError as error:
                FACE_IDENTIFICATION_REQUESTS_TOTAL.labels(outcome="error").inc()
                raise FaceIdentificationResponseError(
                    "얼굴 식별 서비스 응답 JSON이 올바르지 않습니다."
                ) from error
            try:
                observations = self._parse_observations(
                    payload, frame_shape=captured.frame.shape
                )
            except FaceIdentificationError:
                FACE_IDENTIFICATION_REQUESTS_TOTAL.labels(outcome="error").inc()
                raise
            except (TypeError, ValueError) as error:
                FACE_IDENTIFICATION_REQUESTS_TOTAL.labels(outcome="error").inc()
                raise FaceIdentificationResponseError(
                    "얼굴 식별 서비스 응답 형식이 올바르지 않습니다."
                ) from error
            FACE_IDENTIFICATION_REQUESTS_TOTAL.labels(outcome="ok").inc()
            return observations
        finally:
            FACE_IDENTIFICATION_DURATION_SECONDS.observe(
                time.perf_counter() - started_at
            )

    @staticmethod
    def _parse_observations(
        payload: Any,
        *,
        frame_shape: tuple[int, ...],
    ) -> tuple[EntryFaceObservation, ...]:
        try:
            if not isinstance(payload, dict) or set(payload) != {"observations"}:
                raise TypeError
            raw_observations = payload["observations"]
            if not isinstance(raw_observations, list) or len(raw_observations) > 100:
                raise TypeError
            height, width = frame_shape[:2]
            parsed: list[EntryFaceObservation] = []
            seen_track_ids: set[str] = set()
            expected_keys = {
                "face_track_id",
                "face_bbox",
                "detection_confidence",
                "identity_status",
                "student_id",
                "similarity",
                "margin",
                "quality",
                "observation_count",
                "rejected_reason",
            }
            for item in raw_observations:
                if not isinstance(item, dict) or set(item) != expected_keys:
                    raise TypeError
                track_id = item["face_track_id"]
                bbox_value = item["face_bbox"]
                detection_confidence = item["detection_confidence"]
                identity_status = EntryIdentityStatus(item["identity_status"])
                student_id = item["student_id"]
                similarity = item["similarity"]
                margin = item["margin"]
                quality = item["quality"]
                observation_count = item["observation_count"]
                rejected_reason = item["rejected_reason"]

                if (
                    not isinstance(track_id, str)
                    or not track_id
                    or len(track_id) > 128
                    or track_id in seen_track_ids
                    or not isinstance(bbox_value, list)
                    or len(bbox_value) != 4
                    or any(
                        not isinstance(value, int) or isinstance(value, bool)
                        for value in bbox_value
                    )
                    or not (
                        0 <= bbox_value[0] < bbox_value[2] <= width
                        and 0 <= bbox_value[1] < bbox_value[3] <= height
                    )
                    or not _is_number_between(detection_confidence, 0.0, 1.0)
                    or not _is_number_between(quality, 0.0, 1.0)
                    or not isinstance(observation_count, int)
                    or isinstance(observation_count, bool)
                    or observation_count < 0
                    or (
                        rejected_reason is not None
                        and (
                            not isinstance(rejected_reason, str)
                            or not rejected_reason
                            or len(rejected_reason) > 128
                        )
                    )
                ):
                    raise ValueError

                if identity_status is EntryIdentityStatus.REGISTERED:
                    if (
                        not isinstance(student_id, str)
                        or not student_id
                        or len(student_id) > 128
                        or not _is_number_between(similarity, 0.0, 1.0)
                        or not _is_number_between(margin, 0.0, 2.0)
                        or rejected_reason is not None
                    ):
                        raise ValueError
                elif student_id is not None:
                    raise ValueError

                if (similarity is None) != (margin is None):
                    raise ValueError
                if similarity is not None and not _is_number_between(
                    similarity, -1.0, 1.0
                ):
                    raise ValueError
                if margin is not None and not _is_number_between(margin, 0.0, 2.0):
                    raise ValueError

                seen_track_ids.add(track_id)
                parsed.append(
                    EntryFaceObservation(
                        face_track_id=track_id,
                        face_bbox=tuple(bbox_value),  # type: ignore[arg-type]
                        detection_confidence=float(detection_confidence),
                        identity_status=identity_status,
                        student_id=student_id,
                        similarity=(None if similarity is None else float(similarity)),
                        margin=None if margin is None else float(margin),
                        quality=float(quality),
                        observation_count=observation_count,
                        rejected_reason=rejected_reason,
                    )
                )
            return tuple(parsed)
        except (KeyError, TypeError, ValueError):
            raise FaceIdentificationResponseError(
                "얼굴 식별 서비스 응답 형식이 올바르지 않습니다."
            ) from None


def _is_number_between(value: object, minimum: float, maximum: float) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and minimum <= float(value) <= maximum
    )


class EntryFaceProcessor:
    """얼굴 서비스 장애도 저장 가능한 프레임 처리 결과로 바꾼다."""

    def __init__(self, identifier: HttpFaceIdentifier) -> None:
        self._identifier = identifier

    def process(self, captured: CapturedFrame) -> EntryFaceObservationBatch:
        try:
            observations = self._identifier.identify(captured)
        except FaceIdentificationError as error:
            logger.warning(
                "카메라 %s 프레임 %d 얼굴 분석을 완료하지 못했습니다: %s",
                captured.camera_id,
                captured.sequence,
                error,
            )
            return EntryFaceObservationBatch(
                frame_shape=captured.frame.shape,
                processing_status=error.processing_status,
                observations=(),
            )
        return EntryFaceObservationBatch(
            frame_shape=captured.frame.shape,
            processing_status=EntryIdentityProcessingStatus.SUCCEEDED,
            observations=observations,
        )


def build_entry_identity_event_payload(
    captured: CapturedFrame,
    batch: EntryFaceObservationBatch,
) -> dict[str, Any]:
    captured_utc = captured.captured_at.astimezone(UTC)
    captured_milliseconds = int(captured_utc.timestamp() * 1000)
    height, width = batch.frame_shape[:2]
    return {
        "event_id": (
            f"{captured.camera_id}-{captured_milliseconds}-"
            f"{captured.sequence}-entry-face"
        ),
        "camera_id": captured.camera_id,
        "captured_at": captured_utc.isoformat(),
        "sequence": captured.sequence,
        "frame": {"width_pixels": width, "height_pixels": height},
        "processing_status": batch.processing_status.value,
        "observations": [
            {
                "face_track_id": item.face_track_id,
                "face_bbox": list(item.face_bbox),
                "detection_confidence": item.detection_confidence,
                "identity_status": item.identity_status.value,
                "student_id": item.student_id,
                "similarity": item.similarity,
                "margin": item.margin,
                "quality": item.quality,
                "observation_count": item.observation_count,
                "rejected_reason": item.rejected_reason,
            }
            for item in batch.observations
        ],
    }


class FastAPIEntryIdentityEventHandler:
    """입구 관측을 FastAPI에 보내되 메모리 신원 인계를 먼저 진행한다."""

    def __init__(
        self,
        fastapi_url: str,
        *,
        inner: Callable[[CapturedFrame, EntryFaceObservationBatch], None],
        timeout_seconds: float = 5.0,
        max_retries: int = 2,
        backoff_seconds: tuple[float, ...] = (0.2, 0.5),
        post: Callable[..., requests.Response] = requests.post,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if timeout_seconds <= 0 or max_retries < 0 or not backoff_seconds:
            raise ValueError("입구 관측 전송 재시도 설정이 올바르지 않습니다.")
        self._url = fastapi_url.rstrip("/") + ENTRY_IDENTITY_EVENTS_PATH
        self._inner = inner
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._post = post
        self._sleep = sleep

    def __call__(
        self,
        captured: CapturedFrame,
        batch: EntryFaceObservationBatch,
    ) -> None:
        # 저장소 장애가 in-memory 인계를 막지 않도록 순서를 고정한다.
        self._inner(captured, batch)
        payload = build_entry_identity_event_payload(captured, batch)
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = self._post(
                    self._url,
                    json=payload,
                    timeout=self._timeout_seconds,
                )
                response.raise_for_status()
                return
            except Exception as error:
                last_error = error
                if attempt < self._max_retries:
                    wait_seconds = self._backoff_seconds[
                        min(attempt, len(self._backoff_seconds) - 1)
                    ]
                    self._sleep(wait_seconds)
        logger.error(
            "카메라 %s 프레임 %d 입구 관측 저장 실패 (%d회): %s",
            captured.camera_id,
            captured.sequence,
            self._max_retries + 1,
            last_error,
        )


__all__ = [
    "ENTRY_IDENTITY_EVENTS_PATH",
    "EntryFaceProcessor",
    "FaceAnalyzerUnavailableError",
    "FaceIdentificationError",
    "FaceIdentificationResponseError",
    "FastAPIEntryIdentityEventHandler",
    "HttpFaceIdentifier",
    "build_entry_identity_event_payload",
]
