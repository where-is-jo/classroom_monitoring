"""보존 기간 정리 검증. 영상은 정해진 기간이 지나면 반드시 지워져야 한다."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ..retention import RetentionPolicy
from .conftest import FakeStorage

NOW = datetime(2026, 8, 10, 9, 0, 0, tzinfo=UTC)


def build_policy(storage: FakeStorage, *, days: int = 30) -> RetentionPolicy:
    return RetentionPolicy(storage=storage, retention_days=days, now=lambda: NOW)


def test_기한이_지난_객체를_지운다(storage: FakeStorage) -> None:
    storage.add("camera-01/old.mp4", last_modified=NOW - timedelta(days=31))
    storage.add("camera-01/new.mp4", last_modified=NOW - timedelta(days=29))

    result = build_policy(storage).purge()

    assert result.removed == 1
    assert list(storage.objects) == ["camera-01/new.mp4"]


def test_경계에_있는_객체도_지운다(storage: FakeStorage) -> None:
    """정확히 보존 기간이 된 객체를 남기면 기간이 하루 길어진다."""
    storage.add("camera-01/edge.mp4", last_modified=NOW - timedelta(days=30))

    result = build_policy(storage).purge()

    assert result.removed == 1


def test_기한_안의_객체는_건드리지_않는다(storage: FakeStorage) -> None:
    storage.add("camera-01/a.mp4", last_modified=NOW - timedelta(days=1))
    storage.add("camera-01/b.mp4", last_modified=NOW)

    result = build_policy(storage).purge()

    assert result.removed == 0
    assert storage.removed == []
    assert result.inspected == 2


def test_한_객체가_안_지워져도_나머지를_계속_지운다() -> None:
    storage = FakeStorage(fail_keys={"camera-01/locked.mp4"})
    storage.add("camera-01/locked.mp4", last_modified=NOW - timedelta(days=40))
    storage.add("camera-01/other.mp4", last_modified=NOW - timedelta(days=40))

    result = build_policy(storage).purge()

    assert result.removed == 1
    assert result.failed == 1


def test_접두사로_한_카메라만_정리한다(storage: FakeStorage) -> None:
    storage.add("camera-01/old.mp4", last_modified=NOW - timedelta(days=40))
    storage.add("camera-02/old.mp4", last_modified=NOW - timedelta(days=40))

    build_policy(storage).purge(prefix="camera-01/")

    assert list(storage.objects) == ["camera-02/old.mp4"]


def test_보존_기간이_짧을수록_더_많이_지운다(storage: FakeStorage) -> None:
    storage.add("camera-01/a.mp4", last_modified=NOW - timedelta(days=10))
    storage.add("camera-01/b.mp4", last_modified=NOW - timedelta(days=20))

    result = build_policy(storage, days=7).purge()

    assert result.removed == 2


def test_보존_기간이_0이하면_거부한다(storage: FakeStorage) -> None:
    with pytest.raises(ValueError, match="1일 이상"):
        RetentionPolicy(storage=storage, retention_days=0)


def test_빈_저장소도_안전하다(storage: FakeStorage) -> None:
    result = build_policy(storage).purge()

    assert result == type(result)(removed=0, failed=0, inspected=0)
