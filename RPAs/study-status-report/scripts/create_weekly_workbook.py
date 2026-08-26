"""주간 자습 현황 보고서(.xlsx)를 만든다.

**원천은 FastAPI가 아니라 일일 산출물이다.** 보고서가 말하는 "상태 변화"는 이 RPA가
5분마다 폴링해 직접 diff한 파생 계열이다. FastAPI에는 원시 탐지 이벤트만 있어서,
거기서 주간을 다시 만들면 의미가 미묘하게 다른 두 번째 계열이 생기고 **주간 숫자가
이미 보낸 일일 보고서와 어긋난다.** 그래서 일일 보고서를 만든 그 이벤트를 그대로 읽는다
(`data/events_<날짜>_<강의실>.json`, 실행기가 남긴다).

이탈률 정의도 `create_management_workbook.py`와 같은 식을 쓴다 — 한 학생의 이탈 상태
건수를 그 학생의 전체 상태 변화 건수로 나눈다. 주간은 분모가 주 전체가 되므로 일자별
비율의 단순 평균이 아니라 가중 평균이 되는데, 그게 "이 주에 몇 번 중 몇 번 이탈했나"에
맞는 답이다.

**기록이 없는 날을 0으로 세지 않는다.** RPA가 꺼져 있던 날과 이탈이 없던 날은 전혀
다른 이야기인데 둘 다 0으로 적으면 구분이 사라진다. 없는 날은 없다고 적는다.

    python RPAs/study-status-report/scripts/create_weekly_workbook.py \\
      --from 2026-08-24 --to 2026-08-28 --classroom "4A 강의실"
"""

from __future__ import annotations

import argparse
import json
import posixpath
import zipfile
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

# 같은 디렉터리의 일일 스크립트에서 파싱과 XML 조립을 가져온다. 여기서 다시 구현하면
# 이탈률 정의나 셀 조립 규칙이 두 벌이 되어 주간과 일일이 어긋난다.
from create_management_workbook import (
    LEAVE_STATES,
    STATE_LABELS,
    Event,
    parse_events,
    root_rels_xml,
    safe_filename,
    sheet_xml,
    styles_xml,
    workbook_rels_xml,
    workbook_xml,
)

WEEKDAY_LABELS = ("월", "화", "수", "목", "금", "토", "일")
DEFAULT_DATA_DIR = Path("RPAs/study-status-report/data")
DEFAULT_OUTPUT_DIR = Path("RPAs/study-status-report/reports")


class WeeklyReportError(Exception):
    """입력이 잘못됐을 때. 파일이 없는 것과 구분한다."""


def date_range(start: date, end: date) -> list[date]:
    if end < start:
        raise WeeklyReportError("--to는 --from보다 빠를 수 없습니다.")
    if (end - start).days > 62:
        raise WeeklyReportError("한 번에 62일을 넘겨 집계하지 않습니다.")
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def load_day(data_dir: Path, day: date, classroom: str) -> list[Event] | None:
    """그날 이벤트를 읽는다. 파일이 없으면 ``None`` — 0건과 구분하기 위해서다."""
    path = data_dir / f"events_{day.isoformat()}_{safe_filename(classroom)}.json"
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WeeklyReportError(f"{path.name}을(를) 읽지 못했습니다: {error}") from None
    events = raw.get("events") if isinstance(raw, dict) else raw
    if not isinstance(events, list):
        raise WeeklyReportError(f"{path.name}에 events 배열이 없습니다.")
    return parse_events(events)


def leave_rate(leave_count: int, total: int) -> float:
    """일일 보고서와 같은 식. 분모가 0이면 0으로 둔다."""
    return round(leave_count / total * 100, 2) if total else 0


def student_key(event: Event) -> str:
    return event.student_name or event.student_id or "미확인"


def summary_rows(
    classroom: str,
    days: list[date],
    by_day: dict[date, list[Event]],
    missing: list[date],
) -> list[list[Any]]:
    all_events = [event for events in by_day.values() for event in events]
    leave_total = sum(1 for event in all_events if event.student_state in LEAVE_STATES)
    students = {student_key(event) for event in all_events}
    missing_label = ", ".join(day.isoformat() for day in missing) if missing else "없음"
    return [
        [f"{classroom} 주간 자습 현황"],
        [],
        ["기간", f"{days[0].isoformat()} ~ {days[-1].isoformat()}"],
        ["강의실", classroom],
        ["대상 일수", len(days)],
        ["기록이 있는 날", len(by_day)],
        ["기록이 없는 날", missing_label],
        [],
        ["총 상태 변화", len(all_events)],
        ["총 자리 이탈", leave_total],
        ["주간 자리 이탈률(%)", leave_rate(leave_total, len(all_events))],
        ["관측된 학생 수", len(students)],
        [],
        ["기록이 없는 날은 0으로 세지 않았습니다. RPA가 멈춰 있던 날과 이탈이 없던 날은 다릅니다."],
    ]


def daily_rows(days: list[date], by_day: dict[date, list[Event]]) -> list[list[Any]]:
    rows: list[list[Any]] = [["일자별 집계"], [], ["날짜", "요일", "상태 변화", "자리 이탈", "자리 이탈률(%)"]]
    for day in days:
        events = by_day.get(day)
        weekday = WEEKDAY_LABELS[day.weekday()]
        if events is None:
            rows.append([day.isoformat(), weekday, "기록 없음", "기록 없음", "기록 없음"])
            continue
        leave_count = sum(1 for event in events if event.student_state in LEAVE_STATES)
        rows.append([day.isoformat(), weekday, len(events), leave_count, leave_rate(leave_count, len(events))])
    return rows


def student_rows(by_day: dict[date, list[Event]]) -> list[list[Any]]:
    totals: defaultdict[str, int] = defaultdict(int)
    leaves: defaultdict[str, int] = defaultdict(int)
    seats: dict[str, str] = {}
    seen_days: defaultdict[str, set[date]] = defaultdict(set)

    for day, events in by_day.items():
        for event in events:
            key = student_key(event)
            totals[key] += 1
            seen_days[key].add(day)
            if event.seat_number:
                seats.setdefault(key, event.seat_number)
            if event.student_state in LEAVE_STATES:
                leaves[key] += 1

    rows: list[list[Any]] = [
        ["학생별 주간 집계"],
        [],
        ["좌석번호", "학생명", "상태 변화", "자리 이탈", "자리 이탈률(%)", "관측된 날 수"],
    ]
    # 이탈률이 높은 순으로 둔다. 관리자가 먼저 봐야 할 줄이 위에 오게 하려는 것이다.
    ordered = sorted(totals, key=lambda key: (-leave_rate(leaves[key], totals[key]), key))
    for key in ordered:
        rows.append(
            [
                seats.get(key, ""),
                key,
                totals[key],
                leaves[key],
                leave_rate(leaves[key], totals[key]),
                len(seen_days[key]),
            ]
        )
    return rows


def period_rows(by_day: dict[date, list[Event]]) -> list[list[Any]]:
    totals: Counter[str] = Counter()
    leaves: Counter[str] = Counter()
    for events in by_day.values():
        for event in events:
            name = event.period or "미상"
            totals[name] += 1
            if event.student_state in LEAVE_STATES:
                leaves[name] += 1

    rows: list[list[Any]] = [["교시별 주간 집계"], [], ["교시", "상태 변화", "자리 이탈", "자리 이탈률(%)"]]
    for name in sorted(totals):
        rows.append([name, totals[name], leaves[name], leave_rate(leaves[name], totals[name])])
    return rows


def event_rows(days: list[date], by_day: dict[date, list[Event]]) -> list[list[Any]]:
    rows: list[list[Any]] = [
        ["상태 변화 기록"],
        [],
        ["날짜", "교시", "관측시각", "좌석번호", "학생명", "학생 상태"],
    ]
    for day in days:
        for event in by_day.get(day, []):
            rows.append(
                [
                    day.isoformat(),
                    event.period,
                    event.observed_at,
                    event.seat_number,
                    event.student_name,
                    STATE_LABELS.get(event.student_state, event.student_state),
                ]
            )
    return rows


def content_types_xml(sheet_count: int) -> str:
    """주간 워크북 전용.

    일일 쪽 함수를 그대로 쓰지 않는 이유는 그쪽이 drawing1·chart1·chart2를 무조건
    선언하기 때문이다. 주간에는 차트를 넣지 않으므로, 없는 부품을 선언하면 파일이
    깨진다.
    """
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
    ]
    for idx in range(1, sheet_count + 1):
        parts.append(
            f'<Override PartName="/xl/worksheets/sheet{idx}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
    parts.append("</Types>")
    return "".join(parts)


def write_weekly_xlsx(path: Path, sheets: dict[str, list[list[Any]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet_names = list(sheets)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types_xml(len(sheet_names)))
        archive.writestr("_rels/.rels", root_rels_xml())
        archive.writestr("xl/workbook.xml", workbook_xml(sheet_names))
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml(len(sheet_names)))
        archive.writestr("xl/styles.xml", styles_xml())
        for idx, name in enumerate(sheet_names, start=1):
            archive.writestr(
                posixpath.join("xl", "worksheets", f"sheet{idx}.xml"),
                sheet_xml(sheets[name], merge_refs=["A1:F1"], drawing_rel_id=None, column_count=8),
            )


def build_sheets(
    classroom: str, days: list[date], by_day: dict[date, list[Event]], missing: list[date]
) -> dict[str, list[list[Any]]]:
    return {
        "주간 요약": summary_rows(classroom, days, by_day, missing),
        "일자별": daily_rows(days, by_day),
        "학생별": student_rows(by_day),
        "교시별": period_rows(by_day),
        "상태 변화 기록": event_rows(days, by_day),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="start", required=True, help="집계 시작일 (YYYY-MM-DD)")
    parser.add_argument("--to", dest="end", required=True, help="집계 종료일 (YYYY-MM-DD, 포함)")
    parser.add_argument("--classroom", required=True)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end)
    except ValueError:
        raise SystemExit("--from과 --to는 YYYY-MM-DD 형식이어야 합니다.") from None

    try:
        days = date_range(start, end)
        by_day: dict[date, list[Event]] = {}
        missing: list[date] = []
        for day in days:
            events = load_day(args.data_dir, day, args.classroom)
            if events is None:
                missing.append(day)
                continue
            by_day[day] = events
    except WeeklyReportError as error:
        raise SystemExit(str(error)) from None

    if not by_day:
        # 한 날도 없으면 빈 보고서를 만들지 않는다. 받는 사람이 "이탈이 없었다"로
        # 읽을 수 있는데 실제로는 아무것도 관측하지 못한 것이다.
        raise SystemExit(
            f"{start.isoformat()}~{end.isoformat()} 사이에 기록이 하나도 없습니다. "
            f"{args.data_dir}에 events 파일이 있는지 확인하세요."
        )

    out = args.out or args.output_dir / (
        f"weekly_study_status_{start.isoformat()}_{end.isoformat()}_{safe_filename(args.classroom)}.xlsx"
    )
    write_weekly_xlsx(out, build_sheets(args.classroom, days, by_day, missing))
    print(str(out))


if __name__ == "__main__":
    main()
