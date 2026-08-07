"""Fixed, explicitly synthetic demo metadata. No external media or user data."""

from __future__ import annotations

from datetime import UTC, datetime

from .models import DemoStream, DemoStreamStatus, DemoVideoClip

DEMO_STREAMS: tuple[DemoStream, ...] = (
    DemoStream(
        id="demo-stream-a101-front",
        classroom_id="demo-classroom-a101",
        classroom_code="A101",
        classroom_name="A101 일반 강의실",
        camera_label="전면 합성 카메라",
        status=DemoStreamStatus.CONNECTED,
        synthetic_variant="teal-grid",
        poster_path="/demo-assets/demo-poster-a.svg",
        last_updated_at=datetime(2026, 8, 6, 5, 20, tzinfo=UTC),
    ),
    DemoStream(
        id="demo-stream-b203-side",
        classroom_id="demo-classroom-b203",
        classroom_code="B203",
        classroom_name="B203 실습실",
        camera_label="측면 합성 카메라",
        status=DemoStreamStatus.CONNECTED,
        synthetic_variant="indigo-lab",
        poster_path="/demo-assets/demo-poster-b.svg",
        last_updated_at=datetime(2026, 8, 6, 5, 19, tzinfo=UTC),
    ),
    DemoStream(
        id="demo-stream-c305-none",
        classroom_id="demo-classroom-c305",
        classroom_code="C305",
        classroom_name="C305 세미나실",
        camera_label="합성 카메라 없음",
        status=DemoStreamStatus.NO_VIDEO,
        synthetic_variant=None,
        poster_path="/demo-assets/demo-poster-none.svg",
        last_updated_at=datetime(2026, 8, 6, 5, 18, tzinfo=UTC),
    ),
)

DEMO_VIDEO_CLIPS: tuple[DemoVideoClip, ...] = (
    DemoVideoClip(
        id="demo-clip-a101-after-hours",
        title="A101 마감 후 인원 잔류 패턴",
        classroom_id="demo-classroom-a101",
        classroom_code="A101",
        classroom_name="A101 일반 강의실",
        started_at=datetime(2026, 8, 5, 8, 8, tzinfo=UTC),
        ended_at=datetime(2026, 8, 5, 8, 9, 20, tzinfo=UTC),
        tags=("마감 후", "인원 잔류", "좌석 점유"),
        summary="합성 도형 한 개가 마감 시각 이후 좌석 영역에 남아 있는 데모입니다.",
        synthetic_variant="teal-grid",
        poster_path="/demo-assets/demo-poster-a.svg",
    ),
    DemoVideoClip(
        id="demo-clip-a101-entry",
        title="A101 입실 이동 패턴",
        classroom_id="demo-classroom-a101",
        classroom_code="A101",
        classroom_name="A101 일반 강의실",
        started_at=datetime(2026, 8, 5, 0, 58, tzinfo=UTC),
        ended_at=datetime(2026, 8, 5, 1, 0, 5, tzinfo=UTC),
        tags=("입실", "이동", "수업 전"),
        summary="개인정보가 없는 합성 도형의 입실 방향 이동을 표현합니다.",
        synthetic_variant="teal-grid",
        poster_path="/demo-assets/demo-poster-a.svg",
    ),
    DemoVideoClip(
        id="demo-clip-b203-equipment",
        title="B203 실습 장비 구역 움직임",
        classroom_id="demo-classroom-b203",
        classroom_code="B203",
        classroom_name="B203 실습실",
        started_at=datetime(2026, 8, 5, 5, 12, tzinfo=UTC),
        ended_at=datetime(2026, 8, 5, 5, 14, 30, tzinfo=UTC),
        tags=("장비 구역", "이동", "실습"),
        summary="합성 블록이 실습 장비 구역 사이를 이동하는 데모입니다.",
        synthetic_variant="indigo-lab",
        poster_path="/demo-assets/demo-poster-b.svg",
    ),
    DemoVideoClip(
        id="demo-clip-b203-empty",
        title="B203 비어 있는 강의실 패턴",
        classroom_id="demo-classroom-b203",
        classroom_code="B203",
        classroom_name="B203 실습실",
        started_at=datetime(2026, 8, 5, 9, 30, tzinfo=UTC),
        ended_at=datetime(2026, 8, 5, 9, 31, tzinfo=UTC),
        tags=("비어 있음", "움직임 없음", "마감 후"),
        summary="배경 격자만 표시되는 개인정보 없는 빈 공간 데모입니다.",
        synthetic_variant="indigo-lab",
        poster_path="/demo-assets/demo-poster-b.svg",
    ),
)
