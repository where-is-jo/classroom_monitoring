"""deeplearning 얼굴 식별 결과를 사람 탐지에 보강하는 실패 허용 핸들러."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import cv2
import requests

from shared.types import CapturedFrame

from .consumer import ResultHandler
from .metrics import (
    FACE_IDENTIFICATION_DURATION_SECONDS,
    FACE_IDENTIFICATION_REQUESTS_TOTAL,
)
from .types import InferenceResult

logger = logging.getLogger(__name__)

IDENTIFICATION_PATH = "/internal/face-identifications"


class FaceIdentificationError(RuntimeError):
    """얼굴 식별 호출이나 응답 검증이 실패했을 때 발생한다."""


class HttpFaceIdentifier:
    """모델 세부사항 없이 deeplearning의 학생 식별 API만 호출한다."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float,
        jpeg_quality: int,
        minimum_person_confidence: float = 0.0,
        post: Callable[..., requests.Response] = requests.post,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("얼굴 식별 timeout은 0보다 커야 합니다.")
        if not 1 <= jpeg_quality <= 100:
            raise ValueError("얼굴 식별 JPEG 품질은 1과 100 사이여야 합니다.")
        if not 0.0 <= minimum_person_confidence <= 1.0:
            raise ValueError("얼굴 식별 사람 신뢰도는 0과 1 사이여야 합니다.")
        self._url = base_url.rstrip("/") + IDENTIFICATION_PATH
        self._timeout_seconds = timeout_seconds
        self._jpeg_quality = jpeg_quality
        self._minimum_person_confidence = minimum_person_confidence
        self._post = post

    def enrich(
        self, captured: CapturedFrame, result: InferenceResult
    ) -> InferenceResult:
        person_positions = [
            index
            for index, detection in enumerate(result.detections)
            if detection.class_name.casefold() == "person"
            and detection.confidence >= self._minimum_person_confidence
        ]
        if not person_positions:
            return result
        person_bboxes = [result.detections[index].bbox for index in person_positions]
        encoded, buffer = cv2.imencode(
            ".jpg",
            captured.frame,
            [cv2.IMWRITE_JPEG_QUALITY, self._jpeg_quality],
        )
        if not encoded:
            raise FaceIdentificationError("얼굴 식별용 JPEG를 만들지 못했습니다.")

        started_at = time.perf_counter()
        try:
            response = self._post(
                self._url,
                data=bytes(buffer.tobytes()),
                headers={
                    "Content-Type": "image/jpeg",
                    "X-Camera-ID": captured.camera_id,
                    "X-Person-Bboxes": json.dumps(person_bboxes, separators=(",", ":")),
                },
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            matches = self._parse_matches(payload, person_count=len(person_positions))
        except requests.RequestException as error:
            FACE_IDENTIFICATION_REQUESTS_TOTAL.labels(outcome="error").inc()
            raise FaceIdentificationError(
                "얼굴 식별 서비스 호출에 실패했습니다."
            ) from error
        except FaceIdentificationError:
            FACE_IDENTIFICATION_REQUESTS_TOTAL.labels(outcome="error").inc()
            raise
        else:
            FACE_IDENTIFICATION_REQUESTS_TOTAL.labels(outcome="ok").inc()
        finally:
            FACE_IDENTIFICATION_DURATION_SECONDS.observe(
                time.perf_counter() - started_at
            )

        enriched = list(result.detections)
        for person_index, values in matches.items():
            detection_index = person_positions[person_index]
            detection = enriched[detection_index]
            enriched[detection_index] = replace(
                detection,
                student_id=values["student_id"],
                identity_confidence=values["identity_confidence"],
                face_bbox=(
                    values["face_bbox"] if values["student_id"] is not None else None
                ),
                # 사람 ByteTrack이 먼저 붙인 ID가 있으면 그것이 카메라 안에서의
                # 이동 수명이다. 얼굴 track ID로 덮어쓰면 입구에서 확인한 신원을
                # 교실 사람 track으로 인계할 기준이 끊긴다. 단독 얼굴 식별 실행에서만
                # 얼굴 서비스의 track ID를 폴백으로 쓴다.
                track_id=detection.track_id or values["track_id"],
            )
        return InferenceResult(
            frame_shape=result.frame_shape,
            detections=tuple(enriched),
        )

    @staticmethod
    def _parse_matches(payload: Any, *, person_count: int) -> dict[int, dict[str, Any]]:
        try:
            raw_matches = payload["identities"]
            if not isinstance(raw_matches, list):
                raise TypeError
            parsed: dict[int, dict[str, Any]] = {}
            for item in raw_matches:
                if not isinstance(item, dict):
                    raise TypeError
                person_index = item["person_index"]
                face_bbox = tuple(item["face_bbox"])
                track_id = item["track_id"]
                student_id = item.get("student_id")
                confidence = item.get("identity_confidence")
                if (
                    not isinstance(person_index, int)
                    or isinstance(person_index, bool)
                    or not 0 <= person_index < person_count
                    or person_index in parsed
                    or len(face_bbox) != 4
                    or any(
                        not isinstance(value, int) or isinstance(value, bool)
                        for value in face_bbox
                    )
                    or face_bbox[0] >= face_bbox[2]
                    or face_bbox[1] >= face_bbox[3]
                    or not isinstance(track_id, str)
                    or not track_id
                    or len(track_id) > 128
                    or (student_id is None) != (confidence is None)
                    or (
                        student_id is not None
                        and (not isinstance(student_id, str) or not student_id)
                    )
                    or (
                        confidence is not None
                        and (
                            not isinstance(confidence, (int, float))
                            or isinstance(confidence, bool)
                            or not 0.0 <= float(confidence) <= 1.0
                        )
                    )
                ):
                    raise ValueError
                parsed[person_index] = {
                    "face_bbox": face_bbox,
                    "track_id": track_id,
                    "student_id": student_id,
                    "identity_confidence": (
                        None if confidence is None else float(confidence)
                    ),
                }
            return parsed
        except (KeyError, TypeError, ValueError):
            raise FaceIdentificationError(
                "얼굴 식별 서비스 응답 형식이 올바르지 않습니다."
            ) from None


class FaceIdentityResultHandler:
    """식별 실패 시 원래 사람 탐지를 그대로 다음 핸들러로 넘긴다."""

    def __init__(
        self,
        identifier: HttpFaceIdentifier,
        *,
        camera_ids: frozenset[str],
        inner: ResultHandler,
    ) -> None:
        if not camera_ids:
            raise ValueError("얼굴 식별 대상 카메라가 하나 이상 필요합니다.")
        self._identifier = identifier
        self._camera_ids = camera_ids
        self._inner = inner

    def __call__(self, captured: CapturedFrame, result: InferenceResult) -> None:
        if captured.camera_id not in self._camera_ids:
            self._inner(captured, result)
            return
        try:
            enriched = self._identifier.enrich(captured, result)
        except FaceIdentificationError as error:
            # 얼굴 서비스 장애가 사람 탐지·좌석 점유 전송까지 막아서는 안 된다.
            logger.warning(
                "카메라 %s 프레임 %d 얼굴 식별을 건너뜁니다: %s",
                captured.camera_id,
                captured.sequence,
                error,
            )
            enriched = result
        self._inner(captured, enriched)


__all__ = [
    "FaceIdentificationError",
    "FaceIdentityResultHandler",
    "HttpFaceIdentifier",
]
