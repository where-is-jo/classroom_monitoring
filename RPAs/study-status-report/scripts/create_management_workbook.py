"""Create the study status management workbook without third-party packages."""

from __future__ import annotations

import argparse
import base64
import html
import json
import posixpath
import re
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


STATE_LABELS = {
    "PRESENT": "착석",
    "ABSENT": "미착석",
    "WRONG_SEAT": "오착석",
    "IN_CLASSROOM": "강의실 내 좌석 외 위치",
    "UNKNOWN": "판단 보류",
}

REASON_LABELS = {
    "IDENTIFIED_AT_ASSIGNED_SEAT": "배정 좌석에서 학생 식별",
    "IDENTIFIED_AT_OTHER_SEAT": "다른 좌석에서 학생 식별",
    "IDENTIFIED_OUTSIDE_SEATS": "좌석 외 위치에서 학생 식별",
    "IDENTITY_HELD": "직전 식별 결과 유지",
    "SEAT_OCCUPIED_BY_UNKNOWN": "배정 좌석에 미식별 인원 감지",
    "SEAT_VACANT_WITHIN_GRACE": "배정 좌석 비어 있음, 유예 시간 이내",
    "SEAT_VACANT_BEYOND_GRACE": "배정 좌석 비어 있음, 유예 시간 초과",
    "SEAT_NOT_OBSERVED": "좌석 관측 근거 부족",
    "NO_ASSIGNED_SEAT": "배정 좌석 없음",
}


@dataclass(frozen=True)
class Event:
    period: str
    observed_at: str
    seat_number: str
    student_id: str
    student_name: str
    student_state: str
    reason: str
    note: str


def parse_events(data: list[dict[str, Any]]) -> list[Event]:
    events: list[Event] = []
    for raw in data:
        state = str(raw.get("student_state", "UNKNOWN"))
        reason = str(raw.get("reason", "SEAT_NOT_OBSERVED"))
        events.append(
            Event(
                period=str(raw.get("period", "")),
                observed_at=str(raw.get("observed_at", "")),
                seat_number=str(raw.get("seat_number", "")),
                student_id=str(raw.get("student_id", "")),
                student_name=str(raw.get("student_name", "")),
                student_state=state,
                reason=reason,
                note=str(raw.get("note") or REASON_LABELS.get(reason, reason)),
            )
        )
    return events


def load_events(path: Path) -> list[Event]:
    return parse_events(json.loads(path.read_text(encoding="utf-8")))


def load_events_base64(value: str) -> list[Event]:
    decoded = base64.b64decode(value.encode("ascii")).decode("utf-8")
    return parse_events(json.loads(decoded))


def escape(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def col_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def sheet_xml(rows: list[list[Any]], merge_refs: list[str] | None = None) -> str:
    row_xml: list[str] = []
    for row_index, row in enumerate(rows, start=1):
        cells: list[str] = []
        for col_index, value in enumerate(row, start=1):
            ref = f"{col_name(col_index)}{row_index}"
            if isinstance(value, int | float) and not isinstance(value, bool):
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(value)}</t></is></c>')
        row_xml.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    merge_xml = ""
    if merge_refs:
        items = "".join(f'<mergeCell ref="{escape(ref)}"/>' for ref in merge_refs)
        merge_xml = f'<mergeCells count="{len(merge_refs)}">{items}</mergeCells>'

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<sheetViews><sheetView workbookViewId=\"0\"/></sheetViews>"
        "<sheetFormatPr defaultRowHeight=\"18\"/>"
        "<cols><col min=\"1\" max=\"8\" width=\"22\" customWidth=\"1\"/></cols>"
        f"<sheetData>{''.join(row_xml)}</sheetData>"
        f"{merge_xml}"
        "</worksheet>"
    )


def build_rows(report_date: str, classroom: str, events: list[Event]) -> dict[str, list[list[Any]]]:
    latest_by_student: dict[str, Event] = {}
    for event in events:
        latest_by_student[event.student_id or event.student_name] = event

    status_rows: list[list[Any]] = [
        [f"학생 관리 문서 - 날짜: {report_date} / 강의실: {classroom}", "", "", ""],
        ["좌석번호", "학생명", "학생 상태", "상태 판단 근거"],
    ]
    for event in sorted(latest_by_student.values(), key=lambda item: item.seat_number):
        status_rows.append(
            [
                event.seat_number,
                event.student_name,
                STATE_LABELS.get(event.student_state, event.student_state),
                event.note or REASON_LABELS.get(event.reason, event.reason),
            ]
        )

    event_rows: list[list[Any]] = [
        [f"상태 변화 기록 - 날짜: {report_date} / 강의실: {classroom}", "", "", "", "", "", ""],
        ["관측시각", "구간", "좌석번호", "학생명", "학생 상태", "상태 판단 근거", "원본 근거 코드"],
    ]
    for event in events:
        event_rows.append(
            [
                event.observed_at,
                event.period,
                event.seat_number,
                event.student_name,
                STATE_LABELS.get(event.student_state, event.student_state),
                event.note or REASON_LABELS.get(event.reason, event.reason),
                event.reason,
            ]
        )

    state_counts = Counter(event.student_state for event in events)
    event_students = {event.student_id or event.student_name for event in events}
    absent_like = state_counts["ABSENT"] + state_counts["WRONG_SEAT"] + state_counts["IN_CLASSROOM"]
    total_events = max(len(events), 1)
    leave_rate = round(absent_like / total_events * 100, 2)
    per_student = Counter(event.student_name for event in events)

    report_rows: list[list[Any]] = [
        [f"일일 리포트 - 날짜: {report_date} / 강의실: {classroom}", "", ""],
        ["지표", "값", "비고"],
        ["총 상태 변화 건수", len(events), "중복 제거 후 기록 기준"],
        ["영향 학생 수", len(event_students), "상태 변화가 1회 이상 기록된 학생"],
        ["자리 이탈률", f"{leave_rate}%", "ABSENT + WRONG_SEAT + IN_CLASSROOM / 전체 상태 변화"],
        ["미착석 건수", state_counts["ABSENT"], "배정 좌석 미착석"],
        ["오착석 건수", state_counts["WRONG_SEAT"], "다른 좌석 착석"],
        ["좌석 외 위치 건수", state_counts["IN_CLASSROOM"], "강의실 안이지만 좌석 외 위치"],
        ["판단 보류 건수", state_counts["UNKNOWN"], "근거 부족"],
        ["정상 착석 복귀 건수", state_counts["PRESENT"], "배정 좌석 확인"],
        [],
        ["학생별 상태 변화 건수", "", ""],
        ["학생명", "건수", "비고"],
    ]
    for student_name, count in sorted(per_student.items()):
        report_rows.append([student_name, count, ""])

    return {
        "학생 현황": status_rows,
        "상태 변화 기록": event_rows,
        "일일 리포트": report_rows,
    }


def write_xlsx(path: Path, sheets: dict[str, list[list[Any]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet_names = list(sheets)

    content_types = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
    ]
    for idx in range(1, len(sheet_names) + 1):
        content_types.append(
            f'<Override PartName="/xl/worksheets/sheet{idx}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
    content_types.append("</Types>")

    workbook_sheets = "".join(
        f'<sheet name="{escape(name)}" sheetId="{idx}" r:id="rId{idx}"/>'
        for idx, name in enumerate(sheet_names, start=1)
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{workbook_sheets}</sheets>"
        "</workbook>"
    )

    workbook_rels = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
    ]
    for idx in range(1, len(sheet_names) + 1):
        workbook_rels.append(
            f'<Relationship Id="rId{idx}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{idx}.xml"/>'
        )
    workbook_rels.append(
        f'<Relationship Id="rId{len(sheet_names) + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
    )
    workbook_rels.append("</Relationships>")

    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    styles_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
        "</styleSheet>"
    )

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "".join(content_types))
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", "".join(workbook_rels))
        archive.writestr("xl/styles.xml", styles_xml)
        for idx, name in enumerate(sheet_names, start=1):
            merge_ref = "A1:D1" if name == "학생 현황" else "A1:G1"
            if name == "일일 리포트":
                merge_ref = "A1:C1"
            archive.writestr(
                posixpath.join("xl", "worksheets", f"sheet{idx}.xml"),
                sheet_xml(sheets[name], merge_refs=[merge_ref]),
            )


def safe_filename(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣_.-]+", "_", value).strip("_") or "classroom"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--classroom", required=True)
    parser.add_argument("--events", type=Path)
    parser.add_argument("--events-base64")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("RPAs/study-status-report/reports"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.events_base64:
        events = load_events_base64(args.events_base64)
    elif args.events:
        events = load_events(args.events)
    else:
        raise SystemExit("--events or --events-base64 is required")
    output = args.out or args.output_dir / f"study_status_{args.date}_{safe_filename(args.classroom)}.xlsx"
    write_xlsx(output, build_rows(args.date, args.classroom, events))
    print(output)


if __name__ == "__main__":
    main()
