"""계측이 실제 요청 경로에 제대로 붙어 있는지 본다. 모델은 대역으로 바꾼다.

`app.py`는 module import 시점에 mediapipe·insightface를 요구한다. 없는 환경에서는 이
파일 전체를 건너뛴다 — 지표 정의 자체는 `test_metrics.py`가 그 의존 없이 확인한다.

**lifespan을 돌리지 않는다.** 실제 모델 가중치 파일을 요구하기 때문이다. 대신
`app.state`에 대역을 직접 꽂는다.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, ClassVar

import pytest

pytest.importorskip("mediapipe", reason="모델 의존성이 없는 환경에서는 건너뛴다")
pytest.importorskip("insightface", reason="모델 의존성이 없는 환경에서는 건너뛴다")

import cv2
import metrics
import numpy as np
from face_identification import PersonIdentity
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from deeplearning import app as app_module

# 640x480 화면의 가이드 타원 안에 들어오는 얼굴 상자(test_face_guide.py와 같은 값).
_GUIDE_FACE = (220.0, 115.0, 420.0, 365.0)
_SESSION_HEADERS = {"X-Face-Enrollment-ID": "enrollment-metrics"}


class FakeDetector:
    """정해진 탐지 결과나 예외를 내놓는다. ONNX 모델을 로딩하지 않는다."""

    def __init__(
        self,
        detections: Any = None,
        *,
        keypoints: Any = None,
        error: Exception | None = None,
    ) -> None:
        self._detections = np.empty((0, 5)) if detections is None else detections
        self._keypoints = keypoints
        self._error = error

    def detect(self, image: Any, max_num: int = 0) -> tuple[Any, Any]:
        if self._error is not None:
            raise self._error
        return self._detections, self._keypoints


class _EmptyLandmarkResult:
    """랜드마크를 못 찾은 결과. `_head_pose`가 0으로 빠져나간다."""

    face_landmarks: ClassVar[list[Any]] = []
    facial_transformation_matrixes: ClassVar[list[Any]] = []


class FakeLandmarker:
    def detect(self, image: Any) -> _EmptyLandmarkResult:
        return _EmptyLandmarkResult()


def value(name: str, **labels: str) -> float:
    sampled = REGISTRY.get_sample_value(
        f"{metrics.METRIC_PREFIX}{name}", labels or None
    )
    return 0.0 if sampled is None else float(sampled)


def analysis_requests(result: str) -> float:
    return value("face_analysis_requests_total", result=result)


def stage_count(stage: str) -> float:
    return value("face_analysis_duration_seconds_count", stage=stage)


def jpeg(width: int = 640, height: int = 480) -> bytes:
    """합성 회색 이미지. **테스트 자산에 실제 사람의 얼굴을 쓰지 않는다.**"""
    encoded, buffer = cv2.imencode(".jpg", np.full((height, width, 3), 128, np.uint8))
    assert encoded
    return bytes(buffer.tobytes())


@pytest.fixture
def client() -> Iterator[TestClient]:
    app_module._frame_history.clear()
    app_module._fingerprint_history.clear()
    app_module.app.state.face_identification_runtime = None
    # 세션 Gauge는 프로세스에 하나뿐이라 다른 테스트가 바꿔 놓았을 수 있다.
    metrics.install_session_gauge(app_module._active_session_count)
    app_module.app.state.landmarker = FakeLandmarker()
    yield TestClient(app_module.app, raise_server_exceptions=False)
    app_module._frame_history.clear()
    app_module._fingerprint_history.clear()
    app_module.app.state.face_identification_runtime = None


def use_detector(detections: Any = None, **kwargs: Any) -> None:
    app_module.app.state.detector = FakeDetector(detections, **kwargs)


def test_성공한_분석은_구간별로_시간을_남긴다(client: TestClient) -> None:
    use_detector(np.array([[*_GUIDE_FACE, 0.9]]))
    before = {
        stage: stage_count(stage) for stage in ("detect", "quality", "pose", "total")
    }
    before_ok = analysis_requests("ok")

    response = client.post(
        "/internal/face-analysis", content=jpeg(), headers=_SESSION_HEADERS
    )

    assert response.status_code == 200
    assert analysis_requests("ok") == before_ok + 1
    for stage, previous in before.items():
        assert stage_count(stage) == previous + 1, f"{stage} 구간이 기록되지 않았다"


def test_가이드_안에_얼굴이_없으면_실패로_세지_않는다(client: TestClient) -> None:
    """사용자가 아직 자세를 못 잡은 것이지 요청이 잘못된 것이 아니다."""
    use_detector()
    before_no_face = analysis_requests("no_face")
    before_pose = stage_count("pose")

    response = client.post(
        "/internal/face-analysis", content=jpeg(), headers=_SESSION_HEADERS
    )

    assert response.status_code == 200
    assert response.json()["face_count"] == 0
    assert analysis_requests("no_face") == before_no_face + 1
    # 얼굴이 없으면 자세를 재지 않는다. 그 구간이 늘면 계측 위치가 틀린 것이다.
    assert stage_count("pose") == before_pose


def test_세션_ID가_없으면_따로_센다(client: TestClient) -> None:
    use_detector(np.array([[*_GUIDE_FACE, 0.9]]))
    before = analysis_requests("missing_session")

    response = client.post("/internal/face-analysis", content=jpeg())

    assert response.status_code == 400
    assert analysis_requests("missing_session") == before + 1


def test_이미지를_해석하지_못하면_따로_센다(client: TestClient) -> None:
    use_detector(np.array([[*_GUIDE_FACE, 0.9]]))
    before = analysis_requests("bad_image")

    response = client.post(
        "/internal/face-analysis", content=b"not a jpeg", headers=_SESSION_HEADERS
    )

    assert response.status_code == 400
    assert analysis_requests("bad_image") == before + 1


def test_모델이_죽으면_실패로_센다(client: TestClient) -> None:
    """세지 않으면 대시보드에 "느려졌다"만 보이고 "실패하고 있다"는 보이지 않는다."""
    use_detector(error=RuntimeError("ONNX 세션이 죽었다"))
    before = analysis_requests("error")

    response = client.post(
        "/internal/face-analysis", content=jpeg(), headers=_SESSION_HEADERS
    )

    assert response.status_code == 500
    assert analysis_requests("error") == before + 1


def test_정리되지_않은_세션이_지표에_보인다(client: TestClient) -> None:
    """브라우저가 등록 화면을 그냥 닫으면 DELETE가 오지 않아 항목이 남는다."""
    use_detector(np.array([[*_GUIDE_FACE, 0.9]]))

    client.post("/internal/face-analysis", content=jpeg(), headers=_SESSION_HEADERS)

    assert value("face_analysis_sessions_active") == 1

    client.delete("/internal/face-analysis/sessions/enrollment-metrics")

    assert value("face_analysis_sessions_active") == 0


def test_metrics_경로가_지표를_돌려준다(client: TestClient) -> None:
    response = client.get("/metrics")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert f"{metrics.METRIC_PREFIX}face_analysis_requests_total" in response.text


def test_embedding_이미지를_해석하지_못하면_따로_센다(client: TestClient) -> None:
    use_detector()
    before = value("face_embedding_requests_total", result="bad_image")

    response = client.post("/internal/face-embeddings", content=b"not a jpeg")

    assert response.status_code == 400
    assert value("face_embedding_requests_total", result="bad_image") == before + 1


def test_embedding_얼굴이_하나가_아니면_따로_센다(client: TestClient) -> None:
    """뒤로 사람이 지나간 상황이다. 조명 문제와 구분해야 조치가 갈린다."""
    use_detector(np.array([[*_GUIDE_FACE, 0.9], [10.0, 10.0, 60.0, 60.0, 0.8]]))
    before = value("face_embedding_requests_total", result="not_single_face")

    response = client.post("/internal/face-embeddings", content=jpeg())

    assert response.status_code == 422
    assert (
        value("face_embedding_requests_total", result="not_single_face") == before + 1
    )


def test_embedding_신뢰도가_부족하면_따로_센다(client: TestClient) -> None:
    keypoints = np.zeros((1, 5, 2), dtype=np.float32)
    use_detector(np.array([[*_GUIDE_FACE, 0.2]]), keypoints=keypoints)
    before = value("face_embedding_requests_total", result="low_confidence")

    response = client.post("/internal/face-embeddings", content=jpeg())

    assert response.status_code == 422
    assert value("face_embedding_requests_total", result="low_confidence") == before + 1


def test_embedding_요청은_걸린_시간을_남긴다(client: TestClient) -> None:
    use_detector(
        np.array([[*_GUIDE_FACE, 0.2]]), keypoints=np.zeros((1, 5, 2), dtype=np.float32)
    )
    before = value("face_embedding_duration_seconds_count")

    client.post("/internal/face-embeddings", content=jpeg())

    assert value("face_embedding_duration_seconds_count") == before + 1


def test_얼굴_식별_API는_embedding_없이_학생_ID와_bbox만_돌려준다(
    client: TestClient,
) -> None:
    class Runtime:
        def identify(self, **kwargs: Any) -> tuple[PersonIdentity, ...]:
            assert kwargs["camera_id"] == "entry-camera"
            assert kwargs["person_bboxes"] == ((10, 5, 120, 115),)
            return (
                PersonIdentity(
                    person_index=0,
                    face_bbox=(30, 20, 80, 75),
                    track_id=3,
                    student_id="student-a",
                    similarity=0.86,
                ),
            )

    app_module.app.state.face_identification_runtime = Runtime()

    response = client.post(
        "/internal/face-identifications",
        content=jpeg(160, 120),
        headers={
            "X-Camera-ID": "entry-camera",
            "X-Person-Bboxes": "[[10,5,120,115]]",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "identities": [
            {
                "person_index": 0,
                "face_bbox": [30, 20, 80, 75],
                "track_id": "face-3",
                "student_id": "student-a",
                "identity_confidence": 0.86,
            }
        ]
    }
    assert "vector" not in response.text
