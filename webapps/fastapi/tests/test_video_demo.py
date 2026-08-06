"""Synthetic monitoring catalog, search, authorization, and environment gates."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from app.auth.dependencies import get_current_page_user, get_current_user, require_csrf
from app.main import (
    handle_domain_error,
    handle_validation_error,
    include_video_demo_routers,
)
from app.shared.errors import DomainError
from app.shared.templating import STATIC_DIR
from app.users.models import User, UserRole
from app.video_monitoring.errors import VideoSearchInputError
from app.video_monitoring.models import DemoStreamStatus
from app.video_monitoring.service import VideoDemoService
from tests.auth_helpers import build_auth_stack
from tests.settings_helpers import make_settings


@pytest.fixture
def service() -> VideoDemoService:
    return VideoDemoService(clock=lambda: datetime(2026, 8, 6, 5, 30, tzinfo=UTC))


def test_catalog_has_two_synthetic_feeds_one_empty_and_four_clips(
    service: VideoDemoService,
) -> None:
    all_streams = service.list_streams(search=None, classroom_id=None, status=None)
    connected = service.list_streams(
        search=None, classroom_id=None, status=DemoStreamStatus.CONNECTED
    )
    no_video = service.list_streams(
        search=None, classroom_id=None, status=DemoStreamStatus.NO_VIDEO
    )
    all_clips = service.search_videos(
        "데모 영상", classroom_id=None, from_at=None, to_at=None, limit=20
    )

    assert len(all_streams) == 3
    assert len(connected) == 2
    assert len(no_video) == 1
    assert all(item.synthetic_variant for item in connected)
    assert no_video[0].synthetic_variant is None
    assert all_clips.total == 4


def test_korean_metadata_search_room_time_tags_and_empty_result(
    service: VideoDemoService,
) -> None:
    remaining = service.search_videos(
        "어제 5시 이후 A101에 사람이 남아 있던 영상",
        classroom_id=None,
        from_at=None,
        to_at=None,
        limit=20,
    )
    equipment = service.search_videos(
        "B203 장비 구역 이동",
        classroom_id="demo-classroom-b203",
        from_at=datetime(2026, 8, 5, 4, 0, tzinfo=UTC),
        to_at=datetime(2026, 8, 5, 6, 0, tzinfo=UTC),
        limit=20,
    )
    empty = service.search_videos(
        "Z999 존재하지 않는 태그",
        classroom_id=None,
        from_at=None,
        to_at=None,
        limit=20,
    )

    assert [item.clip.id for item in remaining.items] == ["demo-clip-a101-after-hours"]
    assert [item.clip.id for item in equipment.items] == ["demo-clip-b203-equipment"]
    assert "고정 메타데이터 일치" in remaining.items[0].match_reason
    assert empty.total == 0


def test_search_rejects_blank_long_naive_and_inverted_time(service: VideoDemoService) -> None:
    with pytest.raises(VideoSearchInputError):
        service.search_videos(" ", classroom_id=None, from_at=None, to_at=None, limit=20)
    with pytest.raises(VideoSearchInputError):
        service.search_videos("x" * 201, classroom_id=None, from_at=None, to_at=None, limit=20)
    with pytest.raises(VideoSearchInputError, match="timezone"):
        service.search_videos(
            "A101",
            classroom_id=None,
            from_at=datetime(2026, 8, 5, 8, 0),  # noqa: DTZ001
            to_at=None,
            limit=20,
        )
    with pytest.raises(VideoSearchInputError):
        service.search_videos(
            "A101",
            classroom_id=None,
            from_at=datetime(2026, 8, 6, tzinfo=UTC),
            to_at=datetime(2026, 8, 5, tzinfo=UTC),
            limit=20,
        )


def _demo_application(actor: User) -> FastAPI:
    application = FastAPI()
    application.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    include_video_demo_routers(
        application,
        make_settings(
            _env_file=None,
            app_env="local",
            database_mode="memory",
            demo_mode_enabled=True,
        ),
    )
    application.add_exception_handler(
        DomainError,
        handle_domain_error,  # type: ignore[arg-type]
    )
    application.add_exception_handler(
        RequestValidationError,
        handle_validation_error,  # type: ignore[arg-type]
    )
    application.dependency_overrides[get_current_user] = lambda: actor
    application.dependency_overrides[get_current_page_user] = lambda: actor
    application.dependency_overrides[require_csrf] = lambda: None
    return application


@pytest.mark.parametrize("role", [UserRole.STAFF, UserRole.ADMIN])
def test_staff_and_admin_can_use_demo_pages_and_api(role: UserRole) -> None:
    actor = build_auth_stack().seed(role, email=f"video-{role.value.lower()}@example.invalid")
    application = _demo_application(actor)

    with TestClient(application) as client:
        monitoring = client.get("/monitoring")
        search_page = client.get(
            "/video-search",
            params={"query": "어제 5시 이후 A101에 사람이 남아 있던 영상"},
        )
        streams = client.get("/api/v1/video-streams", params={"status": "CONNECTED"})
        search = client.post(
            "/api/v1/video-searches",
            json={"query": "B203 장비 구역 이동", "limit": 10},
        )
        asset = client.get("/demo-assets/demo-video.js")

    assert monitoring.status_code == 200
    assert "실제 CCTV나 실시간 스트림이 아닙니다" in monitoring.text
    assert monitoring.text.count("data-synthetic-video") == 2
    assert "샘플 영상 없음" in monitoring.text
    assert search_page.status_code == 200
    assert "데모 검색 결과" in search_page.text
    assert streams.status_code == 200 and streams.json()["total"] == 2
    assert search.status_code == 200 and search.json()["total"] == 1
    assert search.json()["items"][0]["is_demo"] is True
    assert asset.status_code == 200


def test_student_is_denied_and_api_validation_is_422() -> None:
    student = build_auth_stack().seed(UserRole.STUDENT, email="video-student@example.invalid")
    application = _demo_application(student)

    with TestClient(application, raise_server_exceptions=False) as client:
        denied_page = client.get("/monitoring")
        denied_api = client.get("/api/v1/video-streams")

    assert denied_page.status_code == 403
    assert denied_api.status_code == 403

    staff = build_auth_stack().seed(UserRole.STAFF, email="video-validator@example.invalid")
    validation_app = _demo_application(staff)
    with TestClient(validation_app) as client:
        blank = client.post("/api/v1/video-searches", json={"query": ""})
        too_long = client.post("/api/v1/video-searches", json={"query": "x" * 201})
    assert blank.status_code == 422
    assert too_long.status_code == 422
    assert blank.json()["error"]["code"] == "VALIDATION_ERROR"


def test_disabled_production_registers_neither_demo_routes_nor_assets() -> None:
    application = FastAPI()
    production = make_settings(
        _env_file=None,
        app_env="prod",
        database_mode="mongodb",
        database_url="mongodb://example.invalid",
        database_name="smart_office",
        demo_mode_enabled=False,
    )
    include_video_demo_routers(
        application,
        production.model_copy(update={"demo_mode_enabled": True}),
    )

    with TestClient(application) as client:
        assert client.get("/monitoring").status_code == 404
        assert client.get("/api/v1/video-streams").status_code == 404
        assert client.get("/demo-assets/demo-video.js").status_code == 404
