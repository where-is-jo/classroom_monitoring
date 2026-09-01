"""TASK-006 code-review BLOCKER 회귀: DEMO_MODE_ENABLED=true + memory 모드.

``get_classroom_service``의 ``uow``·``assignment_repository``·``student_lookup``
파라미터 기본값은 ``fastapi.Depends(...)`` 마커다. FastAPI 요청 처리 경로가
아니라 ``initialize_data_store()``처럼 일반 Python 함수 호출로 이 함수를 부르면서
해당 인자를 생략하면, FastAPI DI가 개입하지 않으므로 ``Depends`` 마커 객체
자체가 그대로 서비스에 주입된다. demo seed가 만든 좌석에 대해
``record_observation_batch``가 호출되면 ``_apply_observation``이
``self._uow is not None``을 참으로 보고 ``self._uow.append_history_and_apply_occupancy``를
부르는데, ``Depends`` 객체에는 그 메서드가 없어 ``AttributeError``가 나 앱
기동(``lifespan``) 자체가 크래시한다.

``DEMO_MODE_ENABLED``는 README에 문서화된 정식 로컬 실행 경로이며, 이 저장소의
``conftest.py``는 테스트 스위트 전체에서 그 값을 강제로 꺼둔다
(``os.environ["DEMO_MODE_ENABLED"] = "false"``). 그래서 기존 pytest 스위트는
이 경로를 전혀 실행하지 않았고 회귀가 가려져 있었다. 이 테스트는 그 환경을
명시적으로 재현해 크래시가 없는지, seed된 좌석에 대한 observation batch가
실제로 uow를 거쳐 반영되는지를 검증한다.

module-level ``@lru_cache`` 싱글턴(설정·memory 저장소)이 이 테스트가 만든
demo 데이터로 다른 테스트를 오염시키지 않도록, 테스트 전후로 관련 캐시를
모두 초기화해 격리한다.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.classrooms.models import SeatOccupancy
from app.shared import dependencies


def _clear_memory_singletons() -> None:
    """demo seed 경로가 쓰는 module-level singleton 캐시를 모두 비운다.

    다음 호출에서 ``lru_cache``가 새 빈 인스턴스를 다시 만들도록 해, 이 테스트가
    심은 demo 데이터가 다른 테스트로 새 나가지 않게 한다.
    """
    dependencies.get_settings.cache_clear()
    dependencies._classroom_repository.cache_clear()
    dependencies._seat_assignment_repository.cache_clear()
    dependencies._memory_seat_mutation_uow.cache_clear()
    dependencies._memory_student_repository.cache_clear()
    dependencies._video_stream_repository.cache_clear()


@pytest.fixture
def demo_mode_memory_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """conftest가 강제로 꺼둔 ``DEMO_MODE_ENABLED``를 이 테스트에서만 켠다."""
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("DATABASE_MODE", "memory")
    monkeypatch.setenv("DEMO_MODE_ENABLED", "true")
    _clear_memory_singletons()
    try:
        yield
    finally:
        _clear_memory_singletons()


def test_initialize_data_store_demo_mode_memory_does_not_crash(
    demo_mode_memory_env: None,
) -> None:
    """demo seed가 크래시 없이 끝나고, observation batch가 uow를 거쳐 반영된다."""
    # 크래시(AttributeError) 없이 끝나야 한다. 회귀 시 이 호출에서 바로 예외가 난다.
    dependencies.initialize_data_store()

    settings = dependencies.get_settings()
    assert settings.demo_mode_enabled is True
    assert settings.database_mode == "memory"

    service = dependencies._build_classroom_service(settings)
    page = service.list_classrooms(limit=10, offset=0)
    assert page.items, "demo seed가 강의실을 만들지 않았다"
    classroom_ids = {item.id for item in page.items}
    streams = dependencies._video_stream_repository().find_monitoring_streams()
    assert streams
    assert all(stream.classroom_id in classroom_ids for stream in streams)

    for classroom in page.items:
        summary = service.occupancy_summary(classroom.id)
        assert summary.total > 0
        # observation batch가 uow.append_history_and_apply_occupancy를 거쳐
        # 실제로 커밋됐다면, 최소 하나의 좌석은 더 이상 초기 UNKNOWN 상태가
        # 아니어야 한다(demo_seed는 대부분 좌석을 재석/공석으로 관측한다).
        assert any(
            seat.current_occupancy.state != SeatOccupancy.UNKNOWN for seat in summary.seats
        ), f"{classroom.id}: observation batch가 current occupancy에 반영되지 않았다"
