"""자동 생성 ROI가 실제 MongoDB를 왕복해도 확정 여부를 잃지 않는지 확인한다.

memory 저장소는 dataclass를 그대로 들고 있어 직렬화 결함을 잡지 못한다. 여기서
확인하려는 것은 **`auto_generated`가 문서로 저장되고 다시 읽히는가**다. 이 값이
사라지면 재시작 뒤 아직 확인하지 않은 계산 좌표가 좌석 판정에 들어간다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from app.roi_connections.adapters.mongo import MongoRoiConnectionRepository
from app.roi_connections.models import Point, RoiConnection
from app.shared.database import MongoDatabase

NOW = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)
CLASSROOM_ID = "auto-roi-room"
CAMERA_ID = "auto-roi-camera"


def _connection(seat_id: str, *, auto_generated: bool) -> RoiConnection:
    return RoiConnection(
        classroom_id=CLASSROOM_ID,
        camera_id=CAMERA_ID,
        seat_id=seat_id,
        student_id=None,
        polygon=(
            Point(0.10, 0.10),
            Point(0.30, 0.10),
            Point(0.30, 0.30),
            Point(0.10, 0.30),
        ),
        reference_image_revision=2,
        updated_at=NOW,
        auto_generated=auto_generated,
    )


def test_auto_generated_flag_round_trips_through_mongodb(
    mongo_database: MongoDatabase,
) -> None:
    repository = MongoRoiConnectionRepository(mongo_database)

    repository.save(_connection("auto-seat", auto_generated=True))
    repository.save(_connection("manual-seat", auto_generated=False))

    restored = {
        connection.seat_id: connection
        for connection in repository.list_by_camera(CLASSROOM_ID, CAMERA_ID)
    }
    assert restored["auto-seat"].auto_generated is True
    assert restored["manual-seat"].auto_generated is False
    assert restored["auto-seat"].polygon == _connection("auto-seat", auto_generated=True).polygon


def test_confirming_clears_the_flag_in_storage(mongo_database: MongoDatabase) -> None:
    """확정은 같은 문서를 덮어써야 한다. 새 문서가 생기면 좌석에 ROI가 둘이 된다."""
    repository = MongoRoiConnectionRepository(mongo_database)
    saved = repository.save(_connection("auto-seat", auto_generated=True))

    repository.save(replace(saved, auto_generated=False))

    stored = repository.list_by_camera(CLASSROOM_ID, CAMERA_ID)
    assert len(stored) == 1
    assert stored[0].auto_generated is False


def test_document_written_without_the_flag_reads_back_as_manual(
    mongo_database: MongoDatabase,
) -> None:
    """이 필드가 생기기 전에 저장된 ROI는 사람이 그린 것이다."""
    mongo_database["roi_connections"].insert_one(
        {
            "classroom_id": CLASSROOM_ID,
            "camera_id": CAMERA_ID,
            "seat_id": "legacy-seat",
            "student_id": None,
            "polygon": [
                {"x": 0.1, "y": 0.1},
                {"x": 0.3, "y": 0.1},
                {"x": 0.3, "y": 0.3},
            ],
            "reference_image_revision": 0,
            "updated_at": NOW,
        }
    )

    restored = MongoRoiConnectionRepository(mongo_database).list_by_camera(CLASSROOM_ID, CAMERA_ID)

    assert len(restored) == 1
    assert restored[0].auto_generated is False
