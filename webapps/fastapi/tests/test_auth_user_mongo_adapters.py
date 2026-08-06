"""인증·사용자 MongoDB adapter의 문서·index 계약 테스트."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.audit.adapters.mongo_repository import MongoAuditRepository
from app.audit.models import AuditLog
from app.auth.adapters.mongo_repository import MongoAuthRepository
from app.auth.models import RefreshToken
from app.users.adapters.mongo_repository import MongoUserRepository
from app.users.models import User, UserRole, UserStatus


class RecordingCollection:
    def __init__(self) -> None:
        self.indexes: list[tuple[list[tuple[str, int]], dict[str, object]]] = []

    def create_index(self, fields, **options):
        self.indexes.append((fields, options))


class RecordingDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, RecordingCollection] = {}

    def __getitem__(self, name: str) -> RecordingCollection:
        return self.collections.setdefault(name, RecordingCollection())


def test_Mongo_adapter는_unique와_조회_index를_idempotent_정의한다() -> None:
    database = RecordingDatabase()

    for initializer in (
        MongoUserRepository.ensure_indexes,
        MongoAuthRepository.ensure_indexes,
        MongoAuditRepository.ensure_indexes,
    ):
        initializer(database)  # type: ignore[arg-type]

    user_indexes = database.collections["users"].indexes
    refresh_indexes = database.collections["refresh_tokens"].indexes
    audit_indexes = database.collections["audit_logs"].indexes
    assert any(options.get("unique") and fields == [("email", 1)] for fields, options in user_indexes)
    assert any(options.get("unique") and fields == [("operation_ids", 1)] for fields, options in user_indexes)
    assert any(options.get("unique") and fields == [("token_hash", 1)] for fields, options in refresh_indexes)
    assert any(options.get("unique") and fields == [("operation_id", 1)] for fields, options in audit_indexes)
    assert any(fields == [("family_id", 1), ("revoked_at", 1)] for fields, _ in refresh_indexes)
    assert any(fields[0] == ("resource_type", 1) for fields, _ in audit_indexes)


def test_User_Mongo_document_roundtrip은_domain과_UTC를_보존한다() -> None:
    now = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)
    user = User(
        id="user-id",
        email="user@example.invalid",
        password_hash="$argon2id$redacted",
        name="가상 사용자",
        role=UserRole.STAFF,
        status=UserStatus.ACTIVE,
        failed_login_count=0,
        locked_until=None,
        last_login_at=now,
        created_at=now,
        updated_at=now,
        version=2,
        created_operation_id="create-op",
        last_operation_id="update-op",
    )

    document = MongoUserRepository._to_document(user)
    restored = MongoUserRepository._to_domain(document)

    assert restored == user
    assert restored.created_at.tzinfo is not None


def test_Refresh_Mongo_document에는_token_원문_필드가_없다() -> None:
    now = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)
    refresh_token = RefreshToken(
        id="refresh-id",
        token_hash="sha256-only",
        user_id="user-id",
        family_id="family-id",
        expires_at=now + timedelta(hours=1),
        created_at=now,
    )

    document = MongoAuthRepository._to_document(refresh_token)

    assert MongoAuthRepository._to_domain(document) == refresh_token
    assert document["token_hash"] == "sha256-only"
    assert "token" not in document
    assert "raw" not in document


def test_Audit_Mongo_document는_sanitized_state와_fingerprint만_보존한다() -> None:
    now = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)
    audit_log = AuditLog(
        id="audit-id",
        operation_id="operation-id",
        actor_user_id="actor-id",
        action="USER_UPDATED",
        resource_type="user",
        resource_id="user-id",
        before={"role": "STAFF"},
        after={"role": "ADMIN"},
        ip_fingerprint="hmac-only",
        occurred_at=now,
    )

    document = MongoAuditRepository._to_document(audit_log)

    assert MongoAuditRepository._to_domain(document) == audit_log
    assert "ip" not in document
    assert document["ip_fingerprint"] == "hmac-only"
