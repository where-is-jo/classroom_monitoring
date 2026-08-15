"""ROI 좌표와 좌석-학생 연결 MongoDB 저장소."""

from datetime import datetime

from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from ...shared.database import MongoDatabase, MongoDocument
from ...shared.errors import RepositoryDataError, RepositoryUnavailableError
from ..errors import RoiConnectionConflictError
from ..models import Point, RoiConnection


class MongoRoiConnectionRepository:
    collection_name = "roi_connections"

    def __init__(self, database: MongoDatabase) -> None:
        self._collection = database[self.collection_name]

    @staticmethod
    def ensure_indexes(database: MongoDatabase) -> None:
        collection = database[MongoRoiConnectionRepository.collection_name]
        index_names = collection.index_information()
        for legacy_name in (
            "uq_roi_connections_classroom_seat",
            "uq_roi_connections_classroom_student",
        ):
            if legacy_name in index_names:
                collection.drop_index(legacy_name)
        collection.create_index(
            [
                ("classroom_id", ASCENDING),
                ("camera_id", ASCENDING),
                ("seat_id", ASCENDING),
            ],
            name="uq_roi_connections_classroom_camera_seat",
            unique=True,
            partialFilterExpression={"camera_id": {"$type": "string"}},
        )
        collection.create_index(
            [
                ("classroom_id", ASCENDING),
                ("camera_id", ASCENDING),
                ("student_id", ASCENDING),
            ],
            name="uq_roi_connections_classroom_camera_student",
            unique=True,
            partialFilterExpression={
                "camera_id": {"$type": "string"},
                "student_id": {"$type": "string"},
            },
        )
        collection.create_index(
            [("classroom_id", ASCENDING), ("camera_id", ASCENDING)],
            name="ix_roi_connections_classroom_camera",
        )

    def list_by_classroom(self, classroom_id: str) -> list[RoiConnection]:
        try:
            documents = list(self._collection.find({"classroom_id": classroom_id}))
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return [_to_domain(document) for document in documents]

    def list_by_camera(self, classroom_id: str, camera_id: str) -> list[RoiConnection]:
        try:
            documents = list(
                self._collection.find({"classroom_id": classroom_id, "camera_id": camera_id})
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return [_to_domain(document) for document in documents]

    def find_by_student(
        self, classroom_id: str, camera_id: str, student_id: str
    ) -> RoiConnection | None:
        try:
            document = self._collection.find_one(
                {
                    "classroom_id": classroom_id,
                    "camera_id": camera_id,
                    "student_id": student_id,
                }
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else _to_domain(document)

    def save(self, connection: RoiConnection) -> RoiConnection:
        try:
            document = self._collection.find_one_and_update(
                {
                    "classroom_id": connection.classroom_id,
                    "camera_id": connection.camera_id,
                    "seat_id": connection.seat_id,
                },
                {"$set": _to_document(connection)},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            raise RoiConnectionConflictError(
                "선택한 학생은 이미 다른 좌석에 연결되어 있습니다."
            ) from None
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        if document is None:
            raise RepositoryUnavailableError()
        return _to_domain(document)


def _to_document(connection: RoiConnection) -> MongoDocument:
    return {
        "classroom_id": connection.classroom_id,
        "camera_id": connection.camera_id,
        "seat_id": connection.seat_id,
        "student_id": connection.student_id,
        "polygon": [{"x": point.x, "y": point.y} for point in connection.polygon],
        "reference_image_revision": connection.reference_image_revision,
        "updated_at": connection.updated_at,
    }


def _to_domain(document: MongoDocument) -> RoiConnection:
    try:
        polygon_value = document["polygon"]
        if not isinstance(polygon_value, list):
            raise TypeError
        polygon: list[Point] = []
        for value in polygon_value:
            if not isinstance(value, dict):
                raise TypeError
            x = value.get("x")
            y = value.get("y")
            if not isinstance(x, (int, float)) or isinstance(x, bool):
                raise TypeError
            if not isinstance(y, (int, float)) or isinstance(y, bool):
                raise TypeError
            polygon.append(Point(float(x), float(y)))
        revision = document["reference_image_revision"]
        updated_at = document["updated_at"]
        if not isinstance(revision, int) or isinstance(revision, bool):
            raise TypeError
        if not isinstance(updated_at, datetime) or updated_at.tzinfo is None:
            raise TypeError
        return RoiConnection(
            classroom_id=_required_str(document, "classroom_id"),
            camera_id=_optional_str(document, "camera_id"),
            seat_id=_required_str(document, "seat_id"),
            student_id=_optional_str(document, "student_id"),
            polygon=tuple(polygon),
            reference_image_revision=revision,
            updated_at=updated_at,
        )
    except (KeyError, TypeError, ValueError):
        raise RepositoryDataError() from None


def _required_str(document: MongoDocument, key: str) -> str:
    value = document[key]
    if not isinstance(value, str) or not value:
        raise TypeError
    return value


def _optional_str(document: MongoDocument, key: str) -> str | None:
    value = document.get(key)
    if value is not None and (not isinstance(value, str) or not value):
        raise TypeError
    return value
