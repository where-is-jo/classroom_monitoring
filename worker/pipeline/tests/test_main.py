"""파이프라인 조립 시 결과 핸들러 선택 검증.

실제 모델·카메라·네트워크를 쓰지 않는다. `build_runner` 조립 테스트는 무거운
컴포넌트(모델 로딩, stream worker)를 대역으로 바꿔 넣는다.
"""

from __future__ import annotations

import pytest
from inference.config import InferenceSettings
from inference.consumer import log_result
from inference.face_identity import FaceIdentityResultHandler
from inference.handler import FastAPIResultHandler
from inference.identity_handover import (
    IdentityHandoverResultHandler,
    IdentityHandoverRoute,
)
from inference.snapshot import SnapshotResultHandler
from inference.tracking import ByteTrackResultHandler
from shared.object_storage import ObjectStorageError
from stream.config import StreamSettings

from .. import main as pipeline_main
from ..config import PipelineSettings
from ..runner import PipelineRunner


def build_inference_settings(*, snapshot_enabled: bool = False) -> InferenceSettings:
    # _env_file=None으로 inference/.env를 무시한다. 값은 인자로만 결정한다.
    return InferenceSettings(  # type: ignore[call-arg]
        _env_file=None, snapshot_enabled=snapshot_enabled
    )


def build_pipeline_settings(
    monkeypatch: pytest.MonkeyPatch, *, fastapi_url: str
) -> PipelineSettings:
    # build_runner가 명시 여부를 model_fields_set으로 판단하므로 환경변수로 준다.
    monkeypatch.setenv("FASTAPI_URL", fastapi_url)
    return PipelineSettings(_env_file=None)  # type: ignore[call-arg]


class StubDetector:
    """모델 로딩이 없는 Yolo8nDetector 대역. 빌드 인자만 기록한다."""

    def __init__(
        self,
        *,
        model_path: str,
        device: str,
        confidence_threshold: float,
        image_size: int,
        target_class_ids: dict[int, str],
    ) -> None:
        self.model_path = model_path
        self.device = device
        self.confidence_threshold = confidence_threshold
        self.image_size = image_size
        self.target_class_ids = target_class_ids


class StubStreamWorker:
    """카메라 파이프라인을 만들지 않는 StreamWorker 대역."""

    def __init__(
        self,
        settings: object,
        *,
        publisher: object = None,
        frame_buffer: object = None,
        shutdown_event: object = None,
    ) -> None:
        self.settings = settings
        self.publisher = publisher
        self.frame_buffer = frame_buffer
        self.shutdown_event = shutdown_event


def build_runner(
    monkeypatch: pytest.MonkeyPatch, *, fastapi_url: str | None = None
) -> PipelineRunner:
    """실제 모델 없이 build_runner를 돌리고 핸들러 주입 여부를 본다."""
    if fastapi_url is None:
        monkeypatch.delenv("FASTAPI_URL", raising=False)
        pipeline_settings = PipelineSettings(_env_file=None)  # type: ignore[call-arg]
    else:
        pipeline_settings = build_pipeline_settings(
            monkeypatch, fastapi_url=fastapi_url
        )

    monkeypatch.setattr(pipeline_main, "Yolo8nDetector", StubDetector)
    monkeypatch.setattr(pipeline_main, "StreamWorker", StubStreamWorker)

    return pipeline_main.build_runner(
        stream_settings=StreamSettings(  # type: ignore[call-arg]
            _env_file=None,
            app_env="local",
            stream_sources="camera-01=rtsp://localhost:8554/camera-01",
        ),
        inference_settings=build_inference_settings(),
        pipeline_settings=pipeline_settings,
    )


def test_FASTAPI_URL_미설정이면_log_result를_그대로_쓴다() -> None:
    settings = build_inference_settings()

    handler = pipeline_main.build_result_handler(settings, fastapi_url=None)

    assert handler is log_result


def test_FASTAPI_URL_설정이면_FastAPIResultHandler로_감싼다() -> None:
    settings = build_inference_settings()

    handler = pipeline_main.build_result_handler(
        settings, fastapi_url="http://fastapi:8000"
    )

    assert isinstance(handler, FastAPIResultHandler)
    # 감싼 핸들러는 기존 로그 동작을 먼저 수행한다. 전송이 실패해도 기록이 남는다.
    assert handler._inner is log_result  # type: ignore[attr-defined]


def test_FASTAPI_URL_설정이어도_스냅샷_핸들러를_감싼다() -> None:
    """기존 스냅샷 적재 동작을 해치지 않고 그 위에 전송만 얹는다."""
    settings = build_inference_settings(snapshot_enabled=True)

    handler = pipeline_main.build_result_handler(
        settings, fastapi_url="http://fastapi:8000"
    )

    assert isinstance(handler, FastAPIResultHandler)
    assert isinstance(handler._inner, SnapshotResultHandler)  # type: ignore[attr-defined]


def test_FACE_IDENTITY_URL_설정이면_기존_전송_앞에서_식별을_보강한다() -> None:
    settings = build_inference_settings()

    handler = pipeline_main.build_result_handler(
        settings,
        fastapi_url="http://fastapi:8000",
        face_identity_url="http://deeplearning:8100",
        face_identity_camera_ids=frozenset({"entry-camera"}),
    )

    assert isinstance(handler, FaceIdentityResultHandler)
    assert isinstance(handler._inner, FastAPIResultHandler)  # type: ignore[attr-defined]


def test_FASTAPI_URL_미설정이면_스냅샷_설정은_기존대로_적용된다() -> None:
    """fastapi_url을 안 준다고 스냅샷이 꺼지면 안 된다."""
    settings = build_inference_settings(snapshot_enabled=True)

    handler = pipeline_main.build_result_handler(settings, fastapi_url=None)

    assert isinstance(handler, SnapshotResultHandler)


# ============================================================
# 객체 저장소가 없을 때 (MinIO 미기동)
# ============================================================


def _handler_chain(handler: object) -> list[object]:
    """`inner`로 이어진 결과 핸들러를 바깥부터 안쪽 순서로 편다."""
    chain = [handler]
    while (inner := getattr(chain[-1], "_inner", None)) is not None:
        chain.append(inner)
    return chain


def _fail_to_build_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    """MinIO가 내려가 있을 때와 같은 실패를 만든다.

    실제로는 `ensure_bucket()`이 접속에 실패하면서 이 예외가 나온다.
    """

    def raise_storage_error(*args: object, **kwargs: object) -> object:
        raise ObjectStorageError("버킷을 확인하지 못했습니다: classroom-snapshots")

    monkeypatch.setattr(pipeline_main, "build_object_storage", raise_storage_error)


def test_저장소를_준비하지_못하면_스냅샷만_끄고_계속한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MinIO가 없다고 파이프라인을 멈추지 않는다.

    스냅샷은 결정 0028이 기본값을 꺼 둔 부가 기능이다. 그것 하나 때문에 탐지가
    통째로 멈추면 안 된다.
    """
    _fail_to_build_storage(monkeypatch)
    settings = build_inference_settings(snapshot_enabled=True)

    handler = pipeline_main.build_result_handler(settings, fastapi_url=None)

    assert handler is log_result


def test_저장소가_없어도_FastAPI_전송은_그대로_남는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """이 워커의 본업은 전송이다. 저장소 장애가 본업을 건드리면 안 된다."""
    _fail_to_build_storage(monkeypatch)
    settings = build_inference_settings(snapshot_enabled=True)

    handler = pipeline_main.build_result_handler(
        settings, fastapi_url="http://fastapi:8000"
    )

    assert isinstance(handler, FastAPIResultHandler)
    assert handler._inner is log_result  # type: ignore[attr-defined]


def test_저장소가_없어도_파이프라인_조립이_성공한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """되돌아온 회귀 자체를 고정한다.

    전에는 ObjectStorageError가 build_runner를 그대로 뚫고 나가 main()의
    except (ImportError, OSError)에도 걸리지 않았다. 컨테이너는 traceback을 남기고
    죽은 뒤 재시작만 반복했다.
    """
    _fail_to_build_storage(monkeypatch)
    monkeypatch.delenv("FASTAPI_URL", raising=False)
    monkeypatch.setattr(pipeline_main, "Yolo8nDetector", StubDetector)
    monkeypatch.setattr(pipeline_main, "StreamWorker", StubStreamWorker)

    runner = pipeline_main.build_runner(
        stream_settings=StreamSettings(  # type: ignore[call-arg]
            _env_file=None,
            app_env="local",
            stream_sources="camera-01=rtsp://localhost:8554/camera-01",
        ),
        inference_settings=build_inference_settings(snapshot_enabled=True),
        pipeline_settings=PipelineSettings(_env_file=None),  # type: ignore[call-arg]
    )

    # 핸들러는 여러 겹으로 감싸이므로(ByteTrack 등) 안쪽까지 풀어서 본다.
    # 보는 것은 둘이다 — 조립이 예외 없이 끝났는가, 스냅샷 핸들러가 빠졌는가.
    chain = _handler_chain(runner._consumer._result_handler)  # type: ignore[attr-defined]
    assert not any(isinstance(handler, SnapshotResultHandler) for handler in chain)
    assert chain[-1] is log_result


def test_조립_시_FASTAPI_URL_설정이면_FastAPIResultHandler가_주입된다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = build_runner(monkeypatch, fastapi_url="http://fastapi:8000")

    # 핸들러는 조립 결과로만 확인할 수 있어 소비자 내부 필드를 본다.
    handler = runner._consumer._result_handler  # type: ignore[attr-defined]
    assert isinstance(handler, ByteTrackResultHandler)
    assert isinstance(handler._inner, FastAPIResultHandler)  # type: ignore[attr-defined]
    assert handler._inner._inner is log_result  # type: ignore[attr-defined]


def test_조립_시_FASTAPI_URL_미설정이면_log_result가_주입된다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = build_runner(monkeypatch)

    # 핸들러는 조립 결과로만 확인할 수 있어 소비자 내부 필드를 본다.
    handler = runner._consumer._result_handler  # type: ignore[attr-defined]
    assert isinstance(handler, ByteTrackResultHandler)
    assert handler._inner is log_result  # type: ignore[attr-defined]


def test_조립_시_FASTAPI_URL_빈_문자열이면_전송을_켜지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """빈 값으로 끈 것은 설정한 것으로 치지 않는다. 이전 동작을 유지한다."""
    runner = build_runner(monkeypatch, fastapi_url="")

    # 핸들러는 조립 결과로만 확인할 수 있어 소비자 내부 필드를 본다.
    handler = runner._consumer._result_handler  # type: ignore[attr-defined]
    assert isinstance(handler, ByteTrackResultHandler)
    assert handler._inner is log_result  # type: ignore[attr-defined]


def test_ByteTrack_얼굴식별_인계_FastAPI_순서로_핸들러를_조립한다() -> None:
    settings = build_inference_settings()
    route = IdentityHandoverRoute(
        "entry-camera", "classroom-cctv", (0.0, 0.0, 0.3, 1.0)
    )

    handler = pipeline_main.build_result_handler(
        settings,
        fastapi_url="http://fastapi:8000",
        face_identity_url="http://deeplearning:8100",
        face_identity_camera_ids=frozenset({"entry-camera"}),
        person_tracking_config=pipeline_main.ByteTrackConfig(),
        identity_handover_routes=(route,),
    )

    assert isinstance(handler, ByteTrackResultHandler)
    assert isinstance(handler._inner, FaceIdentityResultHandler)  # type: ignore[attr-defined]
    handover = handler._inner._inner  # type: ignore[attr-defined]
    assert isinstance(handover, IdentityHandoverResultHandler)
    assert isinstance(handover._inner, FastAPIResultHandler)  # type: ignore[attr-defined]


def test_다중_카메라_조립은_카메라마다_최신_프레임을_보존한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FASTAPI_URL", raising=False)
    monkeypatch.setattr(pipeline_main, "Yolo8nDetector", StubDetector)
    monkeypatch.setattr(pipeline_main, "StreamWorker", StubStreamWorker)

    runner = pipeline_main.build_runner(
        stream_settings=StreamSettings(  # type: ignore[call-arg]
            _env_file=None,
            app_env="local",
            stream_sources=(
                "entry-camera=rtsp://localhost:8554/entry-camera,"
                "classroom-cctv=rtsp://host/classroom"
            ),
        ),
        inference_settings=build_inference_settings(),
        pipeline_settings=PipelineSettings(_env_file=None),  # type: ignore[call-arg]
    )

    assert runner.frame_buffer.maxsize == 2
    assert runner.frame_buffer._per_camera is True  # type: ignore[attr-defined]


def test_학습_모델의_탐지_클래스_설정을_detector에_전달한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INFERENCE_TARGET_CLASS_IDS", '{"0":"person"}')

    runner = build_runner(monkeypatch)

    detector = runner._consumer._processor._detector  # type: ignore[attr-defined]
    assert detector.target_class_ids == {0: "person"}


def test_person_클래스_없이_ByteTrack을_켤_수_없다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FASTAPI_URL", raising=False)
    monkeypatch.setattr(pipeline_main, "Yolo8nDetector", StubDetector)
    monkeypatch.setattr(pipeline_main, "StreamWorker", StubStreamWorker)

    with pytest.raises(ValueError, match="person 클래스"):
        pipeline_main.build_runner(
            stream_settings=StreamSettings(  # type: ignore[call-arg]
                _env_file=None,
                app_env="local",
                stream_sources="camera-01=rtsp://localhost:8554/camera-01",
            ),
            inference_settings=InferenceSettings(  # type: ignore[call-arg]
                _env_file=None,
                inference_target_class_ids={7: "student"},
            ),
            pipeline_settings=PipelineSettings(_env_file=None),  # type: ignore[call-arg]
        )


def test_YOLO_임계값이_ByteTrack_high와_같으면_기동하지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FASTAPI_URL", raising=False)
    monkeypatch.setattr(pipeline_main, "Yolo8nDetector", StubDetector)
    monkeypatch.setattr(pipeline_main, "StreamWorker", StubStreamWorker)

    with pytest.raises(ValueError, match="2단계 매칭"):
        pipeline_main.build_runner(
            stream_settings=StreamSettings(  # type: ignore[call-arg]
                _env_file=None,
                app_env="local",
                stream_sources="camera-01=rtsp://localhost:8554/camera-01",
            ),
            inference_settings=InferenceSettings(  # type: ignore[call-arg]
                _env_file=None,
                inference_confidence_threshold=0.5,
            ),
            pipeline_settings=PipelineSettings(_env_file=None),  # type: ignore[call-arg]
        )


def test_얼굴_카메라가_STREAM_SOURCES에_없으면_기동하지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FASTAPI_URL", raising=False)
    monkeypatch.setattr(pipeline_main, "Yolo8nDetector", StubDetector)
    monkeypatch.setattr(pipeline_main, "StreamWorker", StubStreamWorker)

    with pytest.raises(ValueError, match="FACE_IDENTITY_CAMERA_IDS"):
        pipeline_main.build_runner(
            stream_settings=StreamSettings(  # type: ignore[call-arg]
                _env_file=None,
                app_env="local",
                stream_sources="classroom-cctv=rtsp://localhost:8554/classroom-cctv",
            ),
            inference_settings=build_inference_settings(),
            pipeline_settings=PipelineSettings(  # type: ignore[call-arg]
                _env_file=None,
                face_identity_url="http://deeplearning:8100",
                face_identity_camera_ids="entry-camera",
            ),
        )


class MetricsSpy:
    """지표 등록·서버 기동 호출을 기록한다. 전역 레지스트리를 건드리지 않는다."""

    def __init__(self) -> None:
        self.registered: list[object] = []
        self.started: list[tuple[str, int]] = []

    def register(self, frame_buffer: object) -> object:
        self.registered.append(frame_buffer)
        return frame_buffer

    def start(self, *, host: str, port: int) -> bool:
        self.started.append((host, port))
        return True


def install_metrics_spy(monkeypatch: pytest.MonkeyPatch) -> MetricsSpy:
    spy = MetricsSpy()
    monkeypatch.setattr(pipeline_main, "register_frame_buffer", spy.register)
    monkeypatch.setattr(pipeline_main, "start_metrics_server", spy.start)
    return spy


def test_지표를_켜면_버퍼를_등록하고_서버를_띄운다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = build_runner(monkeypatch)
    spy = install_metrics_spy(monkeypatch)
    settings = PipelineSettings(_env_file=None)  # type: ignore[call-arg]

    pipeline_main.enable_metrics(runner, settings)

    assert spy.registered == [runner.frame_buffer]
    assert spy.started == [(settings.metrics_host, settings.metrics_port)]


def test_지표를_끄면_아무것도_열지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """포트를 여는 것은 노출 범위를 넓히는 일이라 끌 수 있어야 한다."""
    runner = build_runner(monkeypatch)
    spy = install_metrics_spy(monkeypatch)
    settings = PipelineSettings(_env_file=None, metrics_enabled=False)  # type: ignore[call-arg]

    pipeline_main.enable_metrics(runner, settings)

    assert spy.registered == []
    assert spy.started == []


def test_탐지_클래스_설정이_그대로_detector에_전달된다(monkeypatch: pytest.MonkeyPatch) -> None:
    """모델을 바꿀 때 클래스 목록이 코드가 아니라 설정으로 따라가야 한다.

    사람만 학습한 전용 모델은 클래스가 0 하나뿐이라, 범용 모델 기준의 67(cell phone)을
    그대로 요구하면 존재하지 않는 클래스를 거르게 된다.
    """
    monkeypatch.setenv("INFERENCE_TARGET_CLASS_IDS", '{"0": "person"}')

    runner = build_runner(monkeypatch)

    # 조립 결과로만 확인할 수 있어 내부 필드를 본다 (위 핸들러 테스트들과 같은 방식).
    detector = runner._consumer._processor._detector  # type: ignore[attr-defined]
    assert detector.target_class_ids == {0: "person"}
