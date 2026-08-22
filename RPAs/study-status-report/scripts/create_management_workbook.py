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
    "IN_CLASSROOM": "좌석 외 위치",
    "UNKNOWN": "판단 보류",
}

LEAVE_STATES = {"ABSENT", "WRONG_SEAT", "IN_CLASSROOM"}


@dataclass(frozen=True)
class Event:
    period: str
    observed_at: str
    seat_number: str
    student_id: str
    student_name: str
    student_state: str


def parse_events(data: list[dict[str, Any]]) -> list[Event]:
    events: list[Event] = []
    for raw in data:
        events.append(
            Event(
                period=str(raw.get("period", "")),
                observed_at=str(raw.get("observed_at", "")),
                seat_number=str(raw.get("seat_number", "")),
                student_id=str(raw.get("student_id", "")),
                student_name=str(raw.get("student_name", "")),
                student_state=str(raw.get("student_state", "UNKNOWN")),
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


def sheet_ref(sheet_name: str, cell_range: str) -> str:
    return "'" + sheet_name.replace("'", "''") + "'!$" + cell_range


def sheet_xml(
    rows: list[list[Any]],
    merge_refs: list[str] | None = None,
    drawing_rel_id: str | None = None,
    column_count: int = 8,
) -> str:
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

    drawing_xml = f'<drawing r:id="{drawing_rel_id}"/>' if drawing_rel_id else ""

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheetViews><sheetView workbookViewId="0"/></sheetViews>'
        '<sheetFormatPr defaultRowHeight="18"/>'
        f'<cols><col min="1" max="{column_count}" width="22" customWidth="1"/></cols>'
        f"<sheetData>{''.join(row_xml)}</sheetData>"
        f"{merge_xml}"
        f"{drawing_xml}"
        "</worksheet>"
    )


def report_data_rows(events: list[Event]) -> list[list[Any]]:
    state_counts = Counter(event.student_state for event in events)
    student_total_counts: defaultdict[str, int] = defaultdict(int)
    student_leave_counts: defaultdict[str, int] = defaultdict(int)

    for event in events:
        student_name = event.student_name or event.student_id or "미확인"
        student_total_counts[student_name] += 1
        if event.student_state in LEAVE_STATES:
            student_leave_counts[student_name] += 1

    rows: list[list[Any]] = [
        ["상태", "건수"],
        ["미착석 건수", state_counts["ABSENT"]],
        ["오착석 건수", state_counts["WRONG_SEAT"]],
        ["좌석 외 위치 건수", state_counts["IN_CLASSROOM"]],
        ["판단 보류 건수", state_counts["UNKNOWN"]],
        ["정상 착석 복귀 건수", state_counts["PRESENT"]],
        [],
        ["학생명", "자리 이탈 건수", "자리 이탈률(%)"],
    ]

    for student_name in sorted(student_total_counts):
        total = student_total_counts[student_name]
        leave_count = student_leave_counts[student_name]
        leave_rate = round(leave_count / total * 100, 2) if total else 0
        rows.append([student_name, leave_count, leave_rate])

    return rows


def build_rows(report_date: str, classroom: str, events: list[Event]) -> dict[str, list[list[Any]]]:
    latest_by_student: dict[str, Event] = {}
    for event in events:
        latest_by_student[event.student_id or event.student_name] = event

    status_rows: list[list[Any]] = [
        [f"학생 관리 문서 - 날짜: {report_date} / 강의실: {classroom}", "", ""],
        ["좌석번호", "학생명", "학생 상태"],
    ]
    for event in sorted(latest_by_student.values(), key=lambda item: item.seat_number):
        status_rows.append(
            [
                event.seat_number,
                event.student_name,
                STATE_LABELS.get(event.student_state, event.student_state),
            ]
        )

    event_rows: list[list[Any]] = [
        [f"상태 변화 기록 - 날짜: {report_date} / 강의실: {classroom}", "", "", "", ""],
        ["관측시각", "구간", "좌석번호", "학생명", "학생 상태"],
    ]
    for event in events:
        event_rows.append(
            [
                event.observed_at,
                event.period,
                event.seat_number,
                event.student_name,
                STATE_LABELS.get(event.student_state, event.student_state),
            ]
        )

    report_rows: list[list[Any]] = [
        [f"일일 리포트 - 날짜: {report_date} / 강의실: {classroom}", "", "", "", "", "", "", ""],
        ["상태별 종합과 학생별 자리 이탈 정도를 그래프로 표시합니다.", "", "", "", "", "", "", ""],
        ["상태별 종합", "", "", "", "학생별 자리 이탈 정도", "", "", ""],
    ]

    return {
        "학생 현황": status_rows,
        "상태 변화 기록": event_rows,
        "일일 리포트": report_rows,
        "리포트 데이터": report_data_rows(events),
    }


def content_types_xml(sheet_count: int) -> str:
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
        '<Override PartName="/xl/drawings/drawing1.xml" ContentType="application/vnd.openxmlformats-officedocument.drawing+xml"/>',
        '<Override PartName="/xl/charts/chart1.xml" ContentType="application/vnd.openxmlformats-officedocument.drawingml.chart+xml"/>',
        '<Override PartName="/xl/charts/chart2.xml" ContentType="application/vnd.openxmlformats-officedocument.drawingml.chart+xml"/>',
    ]
    for idx in range(1, sheet_count + 1):
        parts.append(
            f'<Override PartName="/xl/worksheets/sheet{idx}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
    parts.append("</Types>")
    return "".join(parts)


def workbook_xml(sheet_names: list[str]) -> str:
    sheets = []
    for idx, name in enumerate(sheet_names, start=1):
        state = ' state="hidden"' if name == "리포트 데이터" else ""
        sheets.append(f'<sheet name="{escape(name)}" sheetId="{idx}"{state} r:id="rId{idx}"/>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{''.join(sheets)}</sheets>"
        "</workbook>"
    )


def workbook_rels_xml(sheet_count: int) -> str:
    rels = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
    ]
    for idx in range(1, sheet_count + 1):
        rels.append(
            f'<Relationship Id="rId{idx}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{idx}.xml"/>'
        )
    rels.append(
        f'<Relationship Id="rId{sheet_count + 1}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
    )
    rels.append("</Relationships>")
    return "".join(rels)


def root_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )


def styles_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
        '<borders count="1"><border/></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>'
        "</styleSheet>"
    )


def worksheet_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/drawing" '
        'Target="../drawings/drawing1.xml"/>'
        "</Relationships>"
    )


def drawing_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<xdr:wsDr xmlns:xdr="http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        f"{two_cell_anchor('B', 4, 'G', 19, 'rId1')}"
        f"{two_cell_anchor('B', 22, 'G', 38, 'rId2')}"
        "</xdr:wsDr>"
    )


def two_cell_anchor(from_col: str, from_row: int, to_col: str, to_row: int, rel_id: str) -> str:
    return (
        "<xdr:twoCellAnchor>"
        f"<xdr:from><xdr:col>{ord(from_col) - 65}</xdr:col><xdr:colOff>0</xdr:colOff>"
        f"<xdr:row>{from_row - 1}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>"
        f"<xdr:to><xdr:col>{ord(to_col) - 65}</xdr:col><xdr:colOff>0</xdr:colOff>"
        f"<xdr:row>{to_row - 1}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>"
        '<xdr:graphicFrame macro="">'
        '<xdr:nvGraphicFramePr><xdr:cNvPr id="2" name="Chart"/>'
        "<xdr:cNvGraphicFramePr/></xdr:nvGraphicFramePr>"
        "<xdr:xfrm><a:off x=\"0\" y=\"0\"/><a:ext cx=\"0\" cy=\"0\"/></xdr:xfrm>"
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart">'
        f'<c:chart xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
        f'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:id="{rel_id}"/>'
        "</a:graphicData></a:graphic>"
        "</xdr:graphicFrame>"
        "<xdr:clientData/>"
        "</xdr:twoCellAnchor>"
    )


def drawing_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart" '
        'Target="../charts/chart1.xml"/>'
        '<Relationship Id="rId2" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart" '
        'Target="../charts/chart2.xml"/>'
        "</Relationships>"
    )


def bar_chart_xml(title: str, category_ref: str, value_ref: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<c:chart>"
        f"<c:title><c:tx><c:rich><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>{escape(title)}</a:t></a:r></a:p></c:rich></c:tx></c:title>"
        "<c:plotArea><c:layout/>"
        '<c:barChart><c:barDir val="col"/><c:grouping val="clustered"/>'
        '<c:ser><c:idx val="0"/><c:order val="0"/>'
        f'<c:cat><c:strRef><c:f>{escape(category_ref)}</c:f></c:strRef></c:cat>'
        f'<c:val><c:numRef><c:f>{escape(value_ref)}</c:f></c:numRef></c:val>'
        "</c:ser><c:axId val=\"123456\"/><c:axId val=\"654321\"/></c:barChart>"
        '<c:catAx><c:axId val="123456"/><c:scaling><c:orientation val="minMax"/></c:scaling>'
        '<c:axPos val="b"/><c:tickLblPos val="nextTo"/><c:crossAx val="654321"/></c:catAx>'
        '<c:valAx><c:axId val="654321"/><c:scaling><c:orientation val="minMax"/></c:scaling>'
        '<c:axPos val="l"/><c:majorGridlines/><c:tickLblPos val="nextTo"/><c:crossAx val="123456"/></c:valAx>'
        "</c:plotArea><c:legend><c:legendPos val=\"r\"/></c:legend><c:plotVisOnly val=\"1\"/>"
        "</c:chart>"
        "</c:chartSpace>"
    )


def write_xlsx(path: Path, sheets: dict[str, list[list[Any]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet_names = list(sheets)
    report_sheet_index = sheet_names.index("일일 리포트") + 1

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types_xml(len(sheet_names)))
        archive.writestr("_rels/.rels", root_rels_xml())
        archive.writestr("xl/workbook.xml", workbook_xml(sheet_names))
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml(len(sheet_names)))
        archive.writestr("xl/styles.xml", styles_xml())
        archive.writestr(f"xl/worksheets/_rels/sheet{report_sheet_index}.xml.rels", worksheet_rels_xml())
        archive.writestr("xl/drawings/drawing1.xml", drawing_xml())
        archive.writestr("xl/drawings/_rels/drawing1.xml.rels", drawing_rels_xml())
        archive.writestr(
            "xl/charts/chart1.xml",
            bar_chart_xml(
                "상태별 종합",
                sheet_ref("리포트 데이터", "A$2:$A$6"),
                sheet_ref("리포트 데이터", "B$2:$B$6"),
            ),
        )
        student_rows = max(len(sheets["리포트 데이터"]) - 8, 1)
        last_student_row = 8 + student_rows
        archive.writestr(
            "xl/charts/chart2.xml",
            bar_chart_xml(
                "학생별 자리 이탈 정도",
                sheet_ref("리포트 데이터", f"A$9:$A${last_student_row}"),
                sheet_ref("리포트 데이터", f"B$9:$B${last_student_row}"),
            ),
        )

        for idx, name in enumerate(sheet_names, start=1):
            merge_ref = "A1:C1" if name == "학생 현황" else "A1:E1"
            drawing_rel_id = None
            column_count = 8
            if name == "일일 리포트":
                merge_ref = "A1:H1"
                drawing_rel_id = "rId1"
            archive.writestr(
                posixpath.join("xl", "worksheets", f"sheet{idx}.xml"),
                sheet_xml(
                    sheets[name],
                    merge_refs=[merge_ref],
                    drawing_rel_id=drawing_rel_id,
                    column_count=column_count,
                ),
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
