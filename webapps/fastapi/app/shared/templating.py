"""Jinja2 템플릿 설정.

템플릿 디렉터리는 `app/` 밖에 둔다. Python 코드와 템플릿 파일을 섞지 않기 위해서다.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi.templating import Jinja2Templates

if TYPE_CHECKING:
    from ..classrooms.models import Classroom, ClassroomPage

logger = logging.getLogger(__name__)

# app/shared/templating.py -> webapps/fastapi/
BASE_DIR = Path(__file__).resolve().parent.parent.parent

TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
DEMO_ASSET_DIR = BASE_DIR / "demo_assets"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
KST = timezone(timedelta(hours=9), name="KST")

DISPLAY_LABELS = {
    "SYSTEM": "시스템",
    "MOCK": "합성 관측",
    "OCCUPIED": "재석",
    "VACANT": "부재",
    "UNKNOWN": "확인 필요",
}


def format_kst(value: datetime | None) -> str:
    """저장된 UTC 시각을 화면 표시용 KST로 변환한다."""
    if value is None:
        return "-"
    return value.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S KST")


def format_kst_plain(value: datetime | None) -> str:
    """같은 변환이지만 "KST" 꼬리표를 붙이지 않는다.

    자연어 검색 화면처럼 **그 화면의 모든 시각이 KST인 곳**에서 쓴다. 줄마다 같은
    꼬리표가 붙으면 읽는 눈이 그것을 매번 걸러 내야 하고, 정작 확인해야 할 분·초가
    묻힌다. 시각대를 밝혀야 하는 화면은 `format_kst`를 그대로 쓴다 — 둘을 하나로
    합치지 않는 이유가 그것이다.
    """
    if value is None:
        return "-"
    return value.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")


def format_display_label(value: object) -> str:
    """도메인 enum을 사용자용 한국어 표시명으로 변환한다."""
    raw_value = getattr(value, "value", value)
    if raw_value is None:
        return "-"
    text = str(raw_value)
    return DISPLAY_LABELS.get(text, text)


templates.env.filters["kst_datetime"] = format_kst
templates.env.filters["kst_datetime_plain"] = format_kst_plain
templates.env.filters["display_label"] = format_display_label


def _nav_classrooms() -> list[Classroom]:
    """네비게이션 표시용 강의실 목록. DB 미초기화 시 빈 목록."""
    try:
        from ..main import app
        from ..shared.dependencies import (
            get_classroom_repository,
            get_classroom_service,
            get_settings,
        )

        # 테스트처럼 get_classroom_service가 override되어 있으면 그 서비스의
        # 저장소에서 읽어야 화면이 사용하는 것과 같은 강의실 목록을 얻는다.
        factory = app.dependency_overrides.get(get_classroom_service)
        page: ClassroomPage
        if factory is not None:
            page = factory().list_classrooms(limit=1, offset=0)
        else:
            settings = get_settings()
            service = get_classroom_service(get_classroom_repository(settings), settings=settings)
            page = service.list_classrooms(limit=1, offset=0)
        return page.items
    except Exception:
        logger.debug("nav_classrooms: 강의실 목록 조회 실패", exc_info=True)
        return []


templates.env.globals["nav_classrooms"] = _nav_classrooms
