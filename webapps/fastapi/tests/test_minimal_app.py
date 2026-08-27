"""세 화면과 최소 API의 축소 계약."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.classrooms.adapters.memory_repository import InMemoryClassroomRepository
from app.classrooms.models import (
    CreateClassroomCommand,
    CreateSeatCommand,
    OccupancySource,
    RecordSeatObservationBatchCommand,
    SeatGeometry,
    SeatObservation,
    SeatOccupancy,
)
from app.classrooms.service import ClassroomService
from app.demo_seed import seed_demo_data
from app.main import app
from app.shared.config import Settings
from app.shared.dependencies import (
    get_classroom_service,
    get_video_stream_service,
)
from app.shared.errors import RepositoryUnavailableError
from app.video_monitoring.service import VideoStreamService

NOW = datetime(2026, 8, 10, 3, 0, tzinfo=UTC)


def build_service() -> ClassroomService:
    service = ClassroomService(
        InMemoryClassroomRepository(),
        occupancy_confidence_threshold=0.6,
        clock=lambda: NOW,
    )
    classroom = service.seed_classroom(
        CreateClassroomCommand(id="room-a101", code="A101", name="일반 강의실", location="A동")
    )
    seats = [
        service.seed_seat(
            CreateSeatCommand(
                id=f"seat-{index}",
                classroom_id=classroom.id,
                code=f"S{index:02d}",
                label=f"좌석 {index}",
                geometry=SeatGeometry(x=0.1 + index * 0.2, y=0.2, width=0.15, height=0.2),
            )
        )
        for index in range(1, 4)
    ]
    service.record_observation_batch(
        RecordSeatObservationBatchCommand(
            event_id="observation-current",
            classroom_id=classroom.id,
            source=OccupancySource.SYSTEM,
            observed_at=NOW,
            observations=(
                SeatObservation(seat_id=seats[0].id, occupied=True, confidence=0.95),
                SeatObservation(seat_id=seats[1].id, occupied=False, confidence=0.94),
                SeatObservation(seat_id=seats[2].id, occupied=True, confidence=0.2),
            ),
        )
    )
    return service


@pytest.fixture
def minimal_client() -> Iterator[TestClient]:
    classroom_service = build_service()
    app.dependency_overrides[get_classroom_service] = lambda: classroom_service
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_product_navigation_shows_current_product_sections(minimal_client: TestClient) -> None:
    root = minimal_client.get("/", follow_redirects=False)
    assert root.status_code in {302, 307}
    assert root.headers["location"] == "/classrooms"

    for path, heading in (
        ("/classrooms", "강의실 좌석 현황"),
        ("/monitoring", "실시간 모니터링"),
        ("/llm-search", "자연어 탐지 검색"),
    ):
        response = minimal_client.get(path)
        assert response.status_code == 200
        assert heading in response.text
        # 모니터링 / 등록 관리 / 학생 현황 세 묶음이다.
        assert response.text.count('class="nav-group-title"') == 3
        for label in (
            "강의실 좌석 현황",
            "실시간 모니터링",
            "자연어 탐지 검색",
            "학생 관리",
            "ROI 연결",
            "좌석 관리",
        ):
            assert label in response.text
        for removed in (
            "얼굴 등록",
            "로그인",
            "사용자 관리",
            "직원 관리",
            "면담",
            "알림",
            # 데모 영상 검색은 걷어냈다. 메뉴가 되살아나면 여기서 잡힌다.
            "데모 영상 검색",
            "/video-search",
        ):
            assert removed not in response.text
        assert "set-cookie" not in response.headers


@pytest.mark.parametrize(
    "path",
    [
        "/login",
        "/employees",
        "/admin",
        "/notifications",
        "/events",
        "/classrooms/room-a101",
        "/api/v1/auth/me",
        "/api/v1/users",
        "/api/v1/employees",
        "/api/v1/interview-waits",
        "/api/v1/notifications",
        "/api/v1/admin/dashboard-summary",
        "/api/v1/events",
        "/api/v1/after-hours-alerts",
        "/api/v1/mock-seat-observations",
    ],
)
def test_removed_pages_and_apis_are_404(minimal_client: TestClient, path: str) -> None:
    assert minimal_client.get(path, follow_redirects=False).status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/students/student-removed",
        "/students/create",
        "/students/student-removed/edit",
    ],
)
def test_removed_students_crud_is_404(minimal_client: TestClient, path: str) -> None:
    assert minimal_client.get(path, follow_redirects=False).status_code == 404


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/api/v1/students/student-removed"),
        ("patch", "/api/v1/students/student-removed"),
        ("delete", "/api/v1/students/student-removed"),
    ],
)
def test_removed_students_write_crud_is_404(
    minimal_client: TestClient, method: str, path: str
) -> None:
    response = getattr(minimal_client, method)(path, follow_redirects=False)
    assert response.status_code == 404


def test_openapi_contains_required_domain_apis(minimal_client: TestClient) -> None:
    """아래 경로가 모두 등록되어 있는지만 본다(부분집합).

    이전에는 `paths == {...} <= paths`라는 연쇄 비교여서 "정확히 같다"까지 요구했다.
    그래서 새 기능이 엔드포인트를 더할 때마다 이 테스트가 함께 깨졌다. 이름이 말하는
    계약은 "필수 API가 빠지지 않았다"이므로 부분집합 검사만 남긴다.
    """
    paths = set(minimal_client.get("/openapi.json").json()["paths"])
    assert {
        # 강의실·좌석
        "/api/v1/classrooms",
        "/api/v1/classrooms/{classroom_id}",
        "/api/v1/classrooms/{classroom_id}/occupancy",
        "/api/v1/classrooms/{classroom_id}/occupancy-events",
        "/api/v1/classrooms/{classroom_id}/seat-assignments",
        "/api/v1/classrooms/{classroom_id}/seats",
        "/api/v1/classrooms/{classroom_id}/seats/auto",
        "/api/v1/classrooms/{classroom_id}/seats/migration/preflight",
        "/api/v1/classrooms/{classroom_id}/seats/migration/rollback",
        "/api/v1/classrooms/{classroom_id}/seats/migration/run",
        "/api/v1/classrooms/{classroom_id}/seats/migration/status",
        "/api/v1/classrooms/{classroom_id}/seats/migration/repair/approve",
        "/api/v1/classrooms/{classroom_id}/seats/migration/repair/execute",
        "/api/v1/classrooms/{classroom_id}/seats/migration/repair/request",
        "/api/v1/classrooms/{classroom_id}/seats/{seat_id}",
        "/api/v1/classrooms/{classroom_id}/seats/{seat_id}/assignment",
        # 학생 상태
        "/api/v1/classrooms/{classroom_id}/student-states",
        "/api/v1/classrooms/{classroom_id}/student-state-events",
        # 영상 모니터링
        "/api/v1/video-streams",
        "/api/v1/video-streams/{stream_id}",
        "/api/v1/video-streams/{stream_id}/detections",
        "/api/v1/video-streams/{stream_id}/detection-events",
        "/api/v1/video-streams/{stream_id}/entry-identity-events/stream",
        "/api/v1/video-streams/{stream_id}/playback-sessions",
        "/api/v1/video-streams/{stream_id}/playback-sessions/{session_id}",
        "/api/v1/video-segments",
        # 얼굴 등록
        "/api/v1/students/{student_id}/face-enrollments",
        "/api/v1/students/{student_id}/face-profile",
        "/api/v1/face-enrollments/{enrollment_id}",
        "/api/v1/students",
        "/api/v1/students/{student_id}/face-enrollment",
        "/api/v1/classrooms/{classroom_id}/roi-connection",
        "/api/v1/classrooms/{classroom_id}/roi-connections",
        # 탐지 스냅샷 조회(결정 0011). 영상 원본을 저장하지 않는 대신 남기는 정지 이미지다.
        "/api/v1/snapshots",
        "/api/v1/snapshots/image/{key}",
        # 자연어 탐지 검색. 질문이 본문에 들어가므로 조회지만 POST다.
        "/api/v1/llm-searches",
        "/health",
        "/health/ready",
    } <= paths


def test_seat_summary_maps_present_absent_and_unknown(minimal_client: TestClient) -> None:
    response = minimal_client.get("/api/v1/classrooms/room-a101/occupancy")
    assert response.status_code == 200
    payload = response.json()
    assert (payload["occupied_count"], payload["vacant_count"], payload["unknown_count"]) == (
        1,
        1,
        1,
    )
    assert {item["current_occupancy"]["state"] for item in payload["seats"]} == {
        "OCCUPIED",
        "VACANT",
        "UNKNOWN",
    }


def test_observation_batch_is_idempotent_and_older_data_does_not_replace_current() -> None:
    service = build_service()
    current = service.occupancy_summary("room-a101")
    current_state = current.seats[0].current_occupancy.state
    command = RecordSeatObservationBatchCommand(
        event_id="observation-older",
        classroom_id="room-a101",
        source=OccupancySource.SYSTEM,
        observed_at=NOW - timedelta(minutes=5),
        observations=(SeatObservation(seat_id="seat-1", occupied=False, confidence=0.99),),
    )
    first = service.record_observation_batch(command)
    second = service.record_observation_batch(command)
    assert first == second
    assert service.occupancy_summary("room-a101").seats[0].current_occupancy.state == current_state
    assert current_state == SeatOccupancy.OCCUPIED


def test_empty_provider_keeps_monitoring_page_available(
    minimal_client: TestClient,
) -> None:
    """등록된 카메라가 없어도 화면은 열리고, 빈 상태를 그대로 말한다."""
    monitoring = minimal_client.get("/monitoring")
    streams = minimal_client.get("/api/v1/video-streams")

    assert monitoring.status_code == 200
    assert "연결된 카메라가 없습니다." in monitoring.text
    assert "학생 부재로 해석하지 않습니다" in monitoring.text
    assert streams.json() == {"items": [], "total": 0}


def test_classroom_page_distinguishes_no_classroom_no_seat_and_unobserved_seat(
    minimal_client: TestClient,
) -> None:
    service = ClassroomService(
        InMemoryClassroomRepository(),
        occupancy_confidence_threshold=0.6,
        clock=lambda: NOW,
    )
    app.dependency_overrides[get_classroom_service] = lambda: service
    no_classroom = minimal_client.get("/classrooms")
    assert "표시할 강의실이 없습니다." in no_classroom.text

    service.seed_classroom(
        CreateClassroomCommand(id="room-empty", code="E101", name="빈 강의실", location="E동")
    )
    no_seat = minimal_client.get("/classrooms")
    assert "선택한 강의실에 표시할 좌석이 없습니다." in no_seat.text

    service.seed_seat(
        CreateSeatCommand(
            id="seat-unobserved",
            classroom_id="room-empty",
            code="S01",
            label="좌석 1",
            geometry=None,
        )
    )
    unobserved = minimal_client.get("/api/v1/classrooms/room-empty/occupancy")
    assert unobserved.json()["unknown_count"] == 1
    assert unobserved.json()["seats"][0]["current_occupancy"]["observed_at"] is None


def test_repository_failures_surface_instead_of_empty_results(
    minimal_client: TestClient,
) -> None:
    def unavailable_classroom_service() -> ClassroomService:
        raise RepositoryUnavailableError()

    def unavailable_stream_service() -> VideoStreamService:
        raise RepositoryUnavailableError()

    app.dependency_overrides[get_classroom_service] = unavailable_classroom_service
    classroom = minimal_client.get("/api/v1/classrooms")
    assert classroom.status_code == 503
    assert classroom.json()["error"]["code"] == "REPOSITORY_UNAVAILABLE"

    app.dependency_overrides[get_video_stream_service] = unavailable_stream_service
    monitoring = minimal_client.get("/monitoring")
    assert monitoring.status_code == 503
    assert "요청을 처리할 수 없습니다" in monitoring.text


def test_demo_seed_is_idempotent() -> None:
    service = ClassroomService(
        InMemoryClassroomRepository(),
        occupancy_confidence_threshold=0.6,
        clock=lambda: NOW,
    )
    seed_demo_data(service, now=NOW)
    seed_demo_data(service, now=NOW)
    classrooms = service.list_classrooms(limit=10, offset=0)
    # A101·B203 두 강의실만 시드된다.
    assert classrooms.total == 2
    assert sum(service.occupancy_summary(item.id).total for item in classrooms.items) == 12


def test_occupancy_summary_does_not_truncate_after_one_repository_page() -> None:
    service = ClassroomService(
        InMemoryClassroomRepository(),
        occupancy_confidence_threshold=0.6,
        clock=lambda: NOW,
    )
    service.seed_classroom(
        CreateClassroomCommand(id="room-large", code="L101", name="대형 강의실", location="L동")
    )
    for index in range(201):
        service.seed_seat(
            CreateSeatCommand(
                id=f"large-seat-{index:03d}",
                classroom_id="room-large",
                code=f"S{index:03d}",
                label=f"좌석 {index}",
                geometry=None,
            )
        )

    summary = service.occupancy_summary("room-large")
    assert summary.total == 201
    assert len(summary.seats) == 201
    assert summary.unknown_count == 201


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"app_env": "dev", "database_mode": "memory"}, "APP_ENV=local"),
        (
            {
                "app_env": "prod",
                "database_mode": "mongodb",
                "database_url": SecretStr("mongodb://localhost:27017"),
                "database_name": "learning_monitoring",
                "demo_mode_enabled": True,
            },
            "DEMO_MODE_ENABLED",
        ),
        ({"app_env": "local", "database_mode": "mongodb"}, "DATABASE_URL"),
        (
            {
                "app_env": "local",
                "database_mode": "memory",
                "llm_search_mode": "llama",
                "llm_search_url": "   ",
            },
            "LLM_SEARCH_URL",
        ),
    ],
)
def test_settings_reject_unsafe_or_incomplete_modes(
    kwargs: dict[str, object], message: str
) -> None:
    # _env_file=None으로 로컬 .env.*를 무시한다. 개발자가 DATABASE_URL이 채워진
    # .env.local을 두면 그 값이 검증 대상 값을 덮어써서 테스트가 사람마다 다르게 통과한다.
    with pytest.raises(ValidationError, match=message):
        # _env_file은 pydantic-settings가 런타임에 받는 인자라 Settings 시그니처에 없다.
        Settings(_env_file=None, **kwargs)  # type: ignore[arg-type, call-arg]


def test_pose_quota_total_must_equal_required_sample_count() -> None:
    with pytest.raises(ValidationError, match="Pose quota"):
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            app_env="local",
            database_mode="memory",
            face_enrollment_required_samples=301,
        )
