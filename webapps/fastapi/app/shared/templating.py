"""Jinja2 템플릿 설정.

템플릿 디렉터리는 `app/` 밖에 둔다. Python 코드와 템플릿 파일을 섞지 않기 위해서다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.templating import Jinja2Templates

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


def format_display_label(value: object) -> str:
    """도메인 enum을 사용자용 한국어 표시명으로 변환한다."""
    raw_value = getattr(value, "value", value)
    if raw_value is None:
        return "-"
    text = str(raw_value)
    return DISPLAY_LABELS.get(text, text)


templates.env.filters["kst_datetime"] = format_kst
templates.env.filters["display_label"] = format_display_label
