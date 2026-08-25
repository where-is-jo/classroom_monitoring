"""실제 MongoDB를 쓰는 통합 테스트의 공통 준비.

`TEST_DATABASE_URL`이 없으면 전부 skip한다. database 이름은 `test_`로 시작해야 하며
(`validate_test_database_name`), 그 검사가 개발·운영 database를 향한 실행을 막는다.

**매 테스트마다 대상 collection을 비운다.** 통합 테스트가 서로의 문서를 보면 순서에
따라 결과가 달라져 실패를 재현할 수 없다. database 자체를 drop하지는 않는다 —
index를 매번 다시 만드는 비용이 크고, 이름을 잘못 넣었을 때의 피해가 돌이킬 수 없다.
"""

from __future__ import annotations

import os
import pathlib
from collections.abc import Iterator

import pytest
from pymongo import MongoClient

from app.shared.database import (
    MongoDatabase,
    MongoDocument,
    validate_test_database_name,
)

DEFAULT_TEST_DATABASE_NAME = "test_classroom_monitoring"

# 통합 테스트가 건드리는 collection. 정리 대상을 명시해 두어 실수로 다른 collection을
# 지우지 않게 한다.
MANAGED_COLLECTIONS = (
    "classrooms",
    "seats",
    "seat_assignments",
    "seat_observation_batches",
    "seat_occupancy_history",
    "students",
    "video_streams",
    "roi_connections",
    "detection_events",
    "entry_identity_events",
    "student_states",
    "student_state_history",
)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """이 디렉터리의 테스트에 `mongodb` marker를 일괄로 붙인다.

    conftest의 `pytestmark`는 테스트 모듈에 전파되지 않는다. 파일마다 붙이는 것을
    잊으면 marker 없이 수집돼 "실제 MongoDB가 필요한 테스트"라는 표시가 사라진다.
    """
    for item in items:
        item.add_marker(pytest.mark.mongodb)


# 개발자 로컬 설정. 저장소에 커밋되지 않으며 접속 문자열이 들어간다.
_LOCAL_ENV_FILE = pathlib.Path(__file__).resolve().parents[2] / ".env.local"


def _test_database_url() -> str | None:
    """통합 테스트가 붙을 MongoDB 주소를 찾는다.

    환경변수를 먼저 보고, 없으면 `.env.local`에서 읽는다. 매번 export하지 않아도
    `python -m pytest -q`가 실제 MongoDB에 붙게 하려는 것이다. **값을 로그나 오류
    메시지에 넣지 않는다** — 접속 문자열에 자격 증명이 들어 있다.
    """
    url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if url:
        return url
    return _from_local_env("TEST_DATABASE_URL")


def _from_local_env(key: str) -> str | None:
    if not _LOCAL_ENV_FILE.is_file():
        return None
    for line in _LOCAL_ENV_FILE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        if name.strip() == key:
            return value.strip().strip("\"'") or None
    return None


@pytest.fixture(scope="session")
def mongo_client() -> Iterator[MongoClient[MongoDocument]]:
    url = _test_database_url()
    if url is None:
        pytest.skip("TEST_DATABASE_URL이 없어 MongoDB 통합 테스트를 건너뜁니다.")
    client: MongoClient[MongoDocument] = MongoClient(
        url,
        serverSelectionTimeoutMS=10_000,
        tz_aware=True,
    )
    try:
        client.admin.command("ping")
    except Exception as error:  # pragma: no cover - 접속 실패는 환경 문제다
        client.close()
        pytest.skip(f"MongoDB에 접속할 수 없습니다: {type(error).__name__}")
    yield client
    client.close()


@pytest.fixture(scope="session")
def mongo_database(mongo_client: MongoClient[MongoDocument]) -> MongoDatabase:
    name = (
        os.environ.get("TEST_DATABASE_NAME", "").strip()
        or _from_local_env("TEST_DATABASE_NAME")
        or DEFAULT_TEST_DATABASE_NAME
    )
    validate_test_database_name(name)
    return mongo_client[name]


@pytest.fixture(scope="session")
def mongo_supports_transactions(mongo_client: MongoClient[MongoDocument]) -> bool:
    """replica set이 아니면 좌석 mutation UoW를 만들 수 없다."""
    hello = mongo_client.admin.command("hello")
    return bool(hello.get("setName") or hello.get("msg") == "isdbgrid")


@pytest.fixture(autouse=True)
def clean_collections(mongo_database: MongoDatabase) -> Iterator[None]:
    _clear(mongo_database)
    yield
    _clear(mongo_database)


def _clear(database: MongoDatabase) -> None:
    existing = set(database.list_collection_names())
    for name in MANAGED_COLLECTIONS:
        if name in existing:
            database[name].delete_many({})
