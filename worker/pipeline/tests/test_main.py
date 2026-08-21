"""파이프라인 조립 시 결과 핸들러 선택 검증.

실제 모델·카메라·네트워크를 쓰지 않는다. `build_runner` 조립 테스트는 무거운
컴포넌트(모델 로딩, stream worker)를 대역으로 바꿔 넣는다.
"""

from __future__ import annotations

import pytest
from inference.config import InferenceSettings
from inference.consumer import log_result
from inference.handler import FastAPIResultHandler
from inference.snapshot import SnapshotResultHandler
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
        self, *, model_path: str, device: str, confidence_threshold: float, image_size: int
    ) -> None:
        self.model_path = model_path
        self.device = device
        self.confidence_threshold = confidence_threshold
        self.image_size = image_size


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


def test_FASTAPI_URL_미설정이면_스냅샷_설정은_기존대로_적용된다() -> None:
    """fastapi_url을 안 준다고 스냅샷이 꺼지면 안 된다."""
    settings = build_inference_settings(snapshot_enabled=True)

    handler = pipeline_main.build_result_handler(settings, fastapi_url=None)

    assert isinstance(handler, SnapshotResultHandler)


def test_조립_시_FASTAPI_URL_설정이면_FastAPIResultHandler가_주입된다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = build_runner(monkeypatch, fastapi_url="http://fastapi:8000")

    # 핸들러는 조립 결과로만 확인할 수 있어 소비자 내부 필드를 본다.
    handler = runner._consumer._result_handler  # type: ignore[attr-defined]
    assert isinstance(handler, FastAPIResultHandler)
    assert handler._inner is log_result  # type: ignore[attr-defined]


def test_조립_시_FASTAPI_URL_미설정이면_log_result가_주입된다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = build_runner(monkeypatch)

    # 핸들러는 조립 결과로만 확인할 수 있어 소비자 내부 필드를 본다.
    handler = runner._consumer._result_handler  # type: ignore[attr-defined]
    assert handler is log_result


def test_조립_시_FASTAPI_URL_빈_문자열이면_전송을_켜지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """빈 값으로 끈 것은 설정한 것으로 치지 않는다. 이전 동작을 유지한다."""
    runner = build_runner(monkeypatch, fastapi_url="")

    # 핸들러는 조립 결과로만 확인할 수 있어 소비자 내부 필드를 본다.
    handler = runner._consumer._result_handler  # type: ignore[attr-defined]
    assert handler is log_result


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
