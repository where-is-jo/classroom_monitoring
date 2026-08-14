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
from app.shared.dependencies import get_classroom_service, get_video_demo_service
from app.shared.errors import RepositoryUnavailableError
from app.video_monitoring.service import VideoDemoService

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
    video_service = VideoDemoService(clock=lambda: NOW)
    app.dependency_overrides[get_classroom_service] = lambda: classroom_service
    app.dependency_overrides[get_video_demo_service] = lambda: video_service
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_product_navigation_includes_face_enrollment(minimal_client: TestClient) -> None:
    root = minimal_client.get("/", follow_redirects=False)
    assert root.status_code in {302, 307}
    assert root.headers["location"] == "/classrooms"

    for path, heading in (
        ("/classrooms", "강의실 좌석 현황"),
        ("/monitoring", "실시간 모니터링"),
        # 규칙 기반 데모 카탈로그 검색이다. LLM 검색과 이름이 겹치지 않게 정리했다.
        ("/video-search", "데모 영상 검색"),
        ("/llm-search", "자연어 탐지 검색"),
    ):
        response = minimal_client.get(path)
        assert response.status_code == 200
        assert heading in response.text
        # 모니터링 / 등록 관리 / 학생 현황 세 묶음이다.
        assert response.text.count('class="nav-group-title"') == 3
        # 얼굴 등록은 이 테스트 이름이 약속한 항목이다. 단언이 빠져 있어 함께 넣는다.
        for label in (
            "강의실 좌석 현황",
            "실시간 모니터링",
            "자연어 탐지 검색",
            "데모 영상 검색",
            "얼굴 등록",
        ):
            assert label in response.text
        for removed in ("로그인", "사용자 관리", "직원 관리", "면담", "알림"):
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
        "/api/v1/students",
        "/api/v1/students/student-removed",
        "/students",
        "/students/create",
        "/students/student-removed/edit",
    ],
)
def test_removed_students_crud_is_404(minimal_client: TestClient, path: str) -> None:
    assert minimal_client.get(path, follow_redirects=False).status_code == 404


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/api/v1/students"),
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


def test_openapi_contains_only_minimal_domain_apis(minimal_client: TestClient) -> None:
    paths = set(minimal_client.get("/openapi.json").json()["paths"])
    assert paths == {
        # 강의실·좌석
        "/api/v1/classrooms",
        "/api/v1/classrooms/{classroom_id}",
        "/api/v1/classrooms/{classroom_id}/occupancy",
        "/api/v1/classrooms/{classroom_id}/occupancy-events",
        "/api/v1/classrooms/{classroom_id}/seats",
        "/api/v1/classrooms/{classroom_id}/seats/{seat_id}",
        "/api/v1/classrooms/{classroom_id}/seats/{seat_id}/assignment",
        "/api/v1/classrooms/{classroom_id}/seat-assignments",
        # 학생 상태
        "/api/v1/classrooms/{classroom_id}/student-states",
        # 영상 모니터링
        "/api/v1/video-streams",
        "/api/v1/video-streams/{stream_id}",
        "/api/v1/video-streams/{stream_id}/detections",
        "/api/v1/video-streams/{stream_id}/detection-events",
        "/api/v1/video-searches",
        "/api/v1/video-segments",
        # 얼굴 등록
        "/api/v1/students/{student_id}/face-enrollments",
        "/api/v1/face-enrollments/{enrollment_id}",
        "/api/v1/students/{student_id}/face-profile",
        # 탐지 스냅샷 조회(결정 0011). 영상 원본을 저장하지 않는 대신 남기는 정지 이미지다.
        "/api/v1/snapshots",
        "/api/v1/snapshots/image/{key}",
        # 자연어 탐지 검색. 질문이 본문에 들어가므로 조회지만 POST다.
        "/api/v1/llm-searches",
        # worker가 결과를 밀어 넣는 내부 수집 경로다. 브라우저가 부르는 API가 아니다.
        "/internal/inference/events",
        "/internal/video-segments",
        "/health",
        "/health/ready",
    }


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


def test_natural_language_demo_search_and_validation(minimal_client: TestClient) -> None:
    result = minimal_client.post(
        "/api/v1/video-searches",
        json={"query": "B203 장비 구역 이동", "limit": 10},
    )
    invalid = minimal_client.post("/api/v1/video-searches", json={"query": ""})
    assert result.status_code == 200
    assert result.json()["total"] == 1
    assert result.json()["items"][0]["is_demo"] is True
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"


def test_search_page_accepts_blank_optional_period_fields(minimal_client: TestClient) -> None:
    response = minimal_client.get(
        "/video-search",
        params={"query": "B203 장비 구역 이동", "from": "", "to": "", "limit": 20},
    )
    assert response.status_code == 200
    assert "검색 결과" in response.text
    assert "B203 실습 장비 구역 움직임" in response.text


def test_empty_provider_keeps_monitoring_and_search_pages_available(
    minimal_client: TestClient,
) -> None:
    empty_video_service = VideoDemoService(streams=(), clips=(), clock=lambda: NOW)
    app.dependency_overrides[get_video_demo_service] = lambda: empty_video_service

    monitoring = minimal_client.get("/monitoring")
    search = minimal_client.get("/video-search")
    streams = minimal_client.get("/api/v1/video-streams")
    results = minimal_client.post("/api/v1/video-searches", json={"query": "사람"})

    assert monitoring.status_code == 200
    assert "연결된 영상 source가 없습니다." in monitoring.text
    assert "실제 CCTV나 실시간 스트림" not in monitoring.text
    assert search.status_code == 200
    assert "검색할 운영 metadata가 없습니다." in search.text
    assert streams.json() == {"items": [], "total": 0}
    assert results.status_code == 200
    assert results.json()["items"] == []


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


def test_repository_failures_are_not_replaced_with_demo_data(
    minimal_client: TestClient,
) -> None:
    def unavailable_classroom_service() -> ClassroomService:
        raise RepositoryUnavailableError()

    def unavailable_video_service() -> VideoDemoService:
        raise RepositoryUnavailableError()

    app.dependency_overrides[get_classroom_service] = unavailable_classroom_service
    classroom = minimal_client.get("/api/v1/classrooms")
    assert classroom.status_code == 503
    assert classroom.json()["error"]["code"] == "REPOSITORY_UNAVAILABLE"

    app.dependency_overrides[get_video_demo_service] = unavailable_video_service
    monitoring = minimal_client.get("/monitoring")
    assert monitoring.status_code == 503
    assert "요청을 처리할 수 없습니다" in monitoring.text


def test_search_rejects_reversed_period_and_returns_empty_result(
    minimal_client: TestClient,
) -> None:
    invalid = minimal_client.post(
        "/api/v1/video-searches",
        json={
            "query": "사람",
            "from": "2026-08-10T10:00:00+09:00",
            "to": "2026-08-10T09:00:00+09:00",
        },
    )
    empty = minimal_client.post(
        "/api/v1/video-searches",
        json={"query": "존재하지않는검색어", "limit": 5},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"
    assert empty.status_code == 200
    assert empty.json() == {"items": [], "total": 0, "limit": 5}


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
    ],
)
def test_settings_reject_unsafe_or_incomplete_modes(
    kwargs: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_pose_quota_total_must_equal_required_sample_count() -> None:
    with pytest.raises(ValidationError, match="Pose quota"):
        Settings(
            app_env="local",
            database_mode="memory",
            face_enrollment_required_samples=301,
        )
