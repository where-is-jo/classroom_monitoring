"""스냅샷 적재 판정과 인코딩.

여기서 확인하는 것은 **언제 올리고 언제 올리지 않는가**다. 용량 계산 전체가
이 판정에 기대고 있어서(결정 0011) 조건 하나가 틀어지면 저장소가 넘친다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from shared.object_storage import LocalObjectStorage, ObjectStorageError, StoredObject
from shared.types import CapturedFrame

from ..snapshot import SnapshotResultHandler, encode_snapshot
from ..types import BBox, Detection, InferenceResult


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeStorage:
    def __init__(self, *, fail_times: int = 0) -> None:
        self.puts: list[tuple[str, bytes, str]] = []
        self._fail_times = fail_times

    def put_bytes(self, key: str, data: bytes, *, content_type: str) -> StoredObject:
        if self._fail_times > 0:
            self._fail_times -= 1
            raise ObjectStorageError("적재 실패 대역")
        self.puts.append((key, data, content_type))
        return StoredObject(key=key, size_bytes=len(data), last_modified=datetime.now(UTC))


def build_frame(width: int = 640, height: int = 480) -> np.ndarray:
    # 단색이 아니어야 JPEG 크기가 현실적이다.
    rng = np.random.default_rng(seed=1)
    return rng.integers(0, 255, (height, width, 3), dtype=np.uint8)


def build_captured(camera_id: str = "camera-01", *, sequence: int = 0) -> CapturedFrame:
    return CapturedFrame(
        camera_id=camera_id,
        frame=build_frame(),
        captured_at=datetime(2026, 8, 12, 9, 0, 0, tzinfo=UTC),
        sequence=sequence,
    )


def build_result(count: int) -> InferenceResult:
    bbox: BBox = (0, 0, 10, 10)
    detections = tuple(
        Detection(class_id=0, class_name="person", confidence=0.9, bbox=bbox)
        for _ in range(count)
    )
    return InferenceResult(frame_shape=(480, 640, 3), detections=detections)


def build_handler(
    storage: FakeStorage, clock: FakeClock, *, min_interval_seconds: float = 60.0
) -> SnapshotResultHandler:
    return SnapshotResultHandler(
        storage=storage,  # type: ignore[arg-type]  # 구조만 맞으면 되는 Protocol이다
        min_interval_seconds=min_interval_seconds,
        max_long_side_px=1280,
        jpeg_quality=80,
        inner=lambda captured, result: None,
        monotonic=clock,
    )


def test_기동_직후_탐지가_없으면_올리지_않는다() -> None:
    """아무도 없는 화면을 시작하자마자 한 장 올리는 것은 의미가 없다."""
    storage, clock = FakeStorage(), FakeClock()
    handler = build_handler(storage, clock)

    handler(build_captured(), build_result(0))

    assert storage.puts == []


def test_탐지_개수가_바뀌면_올린다() -> None:
    storage, clock = FakeStorage(), FakeClock()
    handler = build_handler(storage, clock)

    handler(build_captured(), build_result(2))

    assert len(storage.puts) == 1
    key, data, content_type = storage.puts[0]
    assert key == "camera-01/2026-08-12/20260812T090000Z.jpg"
    assert content_type == "image/jpeg"
    assert data.startswith(b"\xff\xd8")  # JPEG SOI 마커


def test_개수가_그대로면_올리지_않는다() -> None:
    storage, clock = FakeStorage(), FakeClock()
    handler = build_handler(storage, clock)

    handler(build_captured(), build_result(2))
    clock.advance(600)
    handler(build_captured(), build_result(2))

    assert len(storage.puts) == 1


def test_최소_간격_안에서는_변화가_있어도_올리지_않는다() -> None:
    storage, clock = FakeStorage(), FakeClock()
    handler = build_handler(storage, clock, min_interval_seconds=60.0)

    handler(build_captured(), build_result(2))
    clock.advance(30)
    handler(build_captured(), build_result(3))

    assert len(storage.puts) == 1


def test_간격이_지나면_건너뛴_변화를_올린다() -> None:
    """캡에 막힌 변화가 사라지면 안 된다. 직전에 '올린' 값과 비교하기 때문에 남는다."""
    storage, clock = FakeStorage(), FakeClock()
    handler = build_handler(storage, clock, min_interval_seconds=60.0)

    handler(build_captured(), build_result(2))
    clock.advance(30)
    handler(build_captured(), build_result(3))  # 캡에 막힘
    clock.advance(31)
    handler(build_captured(), build_result(3))  # 여전히 2와 다르므로 올라간다

    assert len(storage.puts) == 2


def test_적재에_실패하면_상태를_갱신하지_않아_다음에_다시_시도한다() -> None:
    storage, clock = FakeStorage(fail_times=1), FakeClock()
    handler = build_handler(storage, clock, min_interval_seconds=0.001)

    handler(build_captured(), build_result(2))  # 실패
    assert storage.puts == []

    clock.advance(1)
    handler(build_captured(), build_result(2))  # 같은 개수지만 아직 못 올렸으므로 재시도

    assert len(storage.puts) == 1


def test_저장소_장애가_파이프라인을_멈추지_않는다() -> None:
    storage, clock = FakeStorage(fail_times=5), FakeClock()
    handler = build_handler(storage, clock, min_interval_seconds=0.001)

    for index in range(3):
        clock.advance(1)
        handler(build_captured(sequence=index), build_result(2))  # 예외가 새지 않는다

    assert storage.puts == []


def test_카메라별로_따로_판정한다() -> None:
    storage, clock = FakeStorage(), FakeClock()
    handler = build_handler(storage, clock, min_interval_seconds=60.0)

    handler(build_captured("camera-01"), build_result(1))
    handler(build_captured("camera-02"), build_result(1))

    # 한 카메라의 간격 캡이 다른 카메라를 막으면 안 된다.
    assert len(storage.puts) == 2


def test_기존_핸들러를_먼저_호출한다() -> None:
    """스냅샷을 켰다고 탐지 로그가 사라지면 안 된다."""
    calls: list[int] = []
    storage, clock = FakeStorage(), FakeClock()
    handler = SnapshotResultHandler(
        storage=storage,  # type: ignore[arg-type]
        min_interval_seconds=60.0,
        max_long_side_px=1280,
        jpeg_quality=80,
        inner=lambda captured, result: calls.append(len(result.detections)),
        monotonic=clock,
    )

    handler(build_captured(), build_result(0))  # 적재는 건너뛰는 경우
    handler(build_captured(), build_result(2))

    assert calls == [0, 2]


def test_긴_변이_상한을_넘으면_줄인다() -> None:
    data = encode_snapshot(build_frame(1920, 1080), max_long_side_px=1280, jpeg_quality=80)
    decoded = _decode(data)

    assert decoded.shape[1] == 1280
    assert decoded.shape[0] == 720


def test_상한보다_작으면_확대하지_않는다() -> None:
    data = encode_snapshot(build_frame(640, 480), max_long_side_px=1280, jpeg_quality=80)
    decoded = _decode(data)

    assert decoded.shape[:2] == (480, 640)


def test_품질을_낮추면_용량이_줄어든다() -> None:
    frame = build_frame(640, 480)
    high = encode_snapshot(frame, max_long_side_px=1280, jpeg_quality=95)
    low = encode_snapshot(frame, max_long_side_px=1280, jpeg_quality=40)

    assert len(low) < len(high)


def test_로컬_저장소_왕복(tmp_path: Path) -> None:
    """put_bytes로 올린 것이 그대로 다시 읽힌다."""
    storage = LocalObjectStorage(tmp_path)
    payload = b"\xff\xd8snapshot"

    stored = storage.put_bytes("camera-01/2026-08-12/x.jpg", payload, content_type="image/jpeg")

    assert stored.size_bytes == len(payload)
    assert (tmp_path / "camera-01/2026-08-12/x.jpg").read_bytes() == payload
    assert [item.key for item in storage.list_objects("camera-01/")] == [
        "camera-01/2026-08-12/x.jpg"
    ]


def test_로컬_저장소는_루트를_벗어나는_키를_거부한다(tmp_path: Path) -> None:
    storage = LocalObjectStorage(tmp_path)

    with pytest.raises(ObjectStorageError, match="루트를 벗어나는"):
        storage.put_bytes("../escape.jpg", b"x", content_type="image/jpeg")


def _decode(data: bytes) -> np.ndarray:
    import cv2

    decoded = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert decoded is not None
    return decoded
