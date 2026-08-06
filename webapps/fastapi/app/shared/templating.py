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
    "STUDENT": "학생",
    "STAFF": "직원",
    "ADMIN": "관리자",
    "SYSTEM_OPERATOR": "시스템 운영자",
    "ACTIVE": "활성",
    "INACTIVE": "비활성",
    "LOCKED": "잠김",
    "WORKING": "근무 중",
    "ON_CALL": "통화 중",
    "AWAY": "자리 비움",
    "OFFSITE": "외근",
    "MANUAL": "수동 변경",
    "MOCK": "모의 관측",
    "TIME_POLICY": "시간 정책",
    "SYSTEM": "시스템",
    "WAITING": "대기 중",
    "READY": "면담 가능",
    "COMPLETED": "완료",
    "CANCELLED": "취소",
    "EXPIRED": "만료",
    "OCCUPIED": "사용 중",
    "VACANT": "빈 좌석",
    "UNKNOWN": "확인 필요",
    "OPEN": "조치 필요",
    "RESOLVED": "해결됨",
    "SUCCESS": "성공",
    "TEMPORARY_FAILURE": "일시 실패",
    "PERMANENT_FAILURE": "영구 실패",
    "PROCESSING": "처리 중",
    "EMPLOYEE_STATUS": "직원 상태",
    "SEAT_OCCUPANCY": "좌석 상태",
    "INTERVIEW_WAIT": "면담 대기",
    "AFTER_HOURS_ALERT": "마감 후 경고",
    "NOTIFICATION": "알림",
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
