"""MongoDB 신원 인계 설정 저장소."""

from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import PyMongoError

from ...shared.database import MongoDatabase, MongoDocument
from ...shared.errors import RepositoryDataError, RepositoryUnavailableError
from ..models import HandoverZone, IdentityHandoverRoute


class MongoIdentityHandoverRouteRepository:
    collection_name = "identity_handover_routes"

    def __init__(self, database: MongoDatabase) -> None:
        self._collection = database[self.collection_name]

    @staticmethod
    def ensure_indexes(database: MongoDatabase) -> None:
        database[MongoIdentityHandoverRouteRepository.collection_name].create_index(
            [("classroom_id", ASCENDING), ("classroom_camera_id", ASCENDING)],
            name="uq_identity_handover_classroom_camera",
            unique=True,
        )

    def list_all(self) -> list[IdentityHandoverRoute]:
        try:
            documents = list(self._collection.find({}))
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return [_to_domain(document) for document in documents]

    def list_by_classroom(self, classroom_id: str) -> list[IdentityHandoverRoute]:
        try:
            documents = list(self._collection.find({"classroom_id": classroom_id}))
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return [_to_domain(document) for document in documents]

    def find_by_classroom_camera(
        self, classroom_id: str, classroom_camera_id: str
    ) -> IdentityHandoverRoute | None:
        try:
            document = self._collection.find_one(
                {
                    "classroom_id": classroom_id,
                    "classroom_camera_id": classroom_camera_id,
                }
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return None if document is None else _to_domain(document)

    def save(self, route: IdentityHandoverRoute) -> IdentityHandoverRoute:
        try:
            document = self._collection.find_one_and_update(
                {
                    "classroom_id": route.classroom_id,
                    "classroom_camera_id": route.classroom_camera_id,
                },
                {"$set": _to_document(route)},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        if document is None:
            raise RepositoryUnavailableError()
        return _to_domain(document)

    def delete(self, classroom_id: str, classroom_camera_id: str) -> bool:
        try:
            result = self._collection.delete_one(
                {
                    "classroom_id": classroom_id,
                    "classroom_camera_id": classroom_camera_id,
                }
            )
        except PyMongoError:
            raise RepositoryUnavailableError() from None
        return result.deleted_count > 0


def _to_document(route: IdentityHandoverRoute) -> MongoDocument:
    return {
        "classroom_id": route.classroom_id,
        "entry_camera_id": route.entry_camera_id,
        "classroom_camera_id": route.classroom_camera_id,
        "classroom_entry_zone": list(route.classroom_entry_zone.as_tuple()),
        "reference_image_revision": route.reference_image_revision,
        "updated_at": route.updated_at,
    }


def _to_domain(document: MongoDocument) -> IdentityHandoverRoute:
    try:
        raw_zone = document["classroom_entry_zone"]
        if not isinstance(raw_zone, list) or len(raw_zone) != 4:
            raise TypeError
        zone = tuple(float(value) for value in raw_zone)
        return IdentityHandoverRoute(
            classroom_id=str(document["classroom_id"]),
            entry_camera_id=str(document["entry_camera_id"]),
            classroom_camera_id=str(document["classroom_camera_id"]),
            classroom_entry_zone=HandoverZone(*zone),
            reference_image_revision=int(document["reference_image_revision"]),
            updated_at=document["updated_at"],
        )
    except (KeyError, TypeError, ValueError):
        raise RepositoryDataError() from None
