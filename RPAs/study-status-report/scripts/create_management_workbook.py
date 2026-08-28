"""일일 자습 현황 관리 문서(.xlsx)를 외부 패키지 없이 만든다.

**서식은 ``xlsx_style``이 정한다.** 여기서 색을 직접 고르지 않는 이유는 주간 워크북과
같은 상태가 다른 색으로 나오면 받는 사람이 그 차이를 의미로 읽기 때문이다.

**'리포트 데이터' 시트의 줄 배치는 건드리지 않는다.** 차트가 ``A$2:$A$6`` 처럼 셀 주소로
계열을 가리켜서, 한 줄만 밀려도 차트가 엉뚱한 값을 그린다. 그래서 이 시트만 서식 없이
예전 배치를 그대로 둔다.
"""

from __future__ import annotations

import argparse
import base64
import json
import posixpath
import re
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from xlsx_style import (
    KST,
    STATE_SEVERITY_ORDER,
    Cell,
    Palette,
    StyleBook,
    escape,
    sheet_xml,
    to_kst_text,
)

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


@dataclass
class SheetSpec:
    """시트 하나의 내용과 화면 설정.

    행 목록만 넘기던 것을 묶은 이유는, 틀 고정 위치와 자동 필터 범위가 **머리글이 몇 번째
    줄인지**에 달려 있어서다. 제목 줄을 하나 넣으면 둘 다 같이 밀려야 하는데, 따로 두면
    한쪽만 고치고 지나가기 쉽다.
    """

    rows: list[list[Any]]
    col_widths: list[float] = field(default_factory=list)
    freeze_row: int | None = None
    autofilter_ref: str | None = None
    merge_refs: list[str] = field(default_factory=list)
    row_heights: dict[int, float] = field(default_factory=dict)
    drawing_rel_id: str | None = None


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


def state_label(state: str) -> str:
    return STATE_LABELS.get(state, state)


def sheet_ref(sheet_name: str, cell_range: str) -> str:
    return "'" + sheet_name.replace("'", "''") + "'!$" + cell_range


def generated_stamp() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M")


def title_block(
    palette: Palette, title: str, meta: str, width: int
) -> tuple[list[list[Any]], list[str], dict[int, float]]:
    """모든 시트가 같은 머리를 갖게 한다 — 제목 한 줄, 메타 한 줄, 빈 줄."""
    last = chr(64 + width)
    rows: list[list[Any]] = [
        [Cell(title, palette.title)],
        [Cell(meta, palette.subtitle)],
        [],
    ]
    merges = [f"A1:{last}1", f"A2:{last}2"]
    return rows, merges, {1: 30, 2: 16, 3: 6}


def legend_row(palette: Palette, counts: Counter[str]) -> list[Any]:
    """상태 배지를 심각도 순으로 늘어놓는다. 범례이면서 동시에 요약이다.

    색만 있는 범례는 자리를 먹고 아무것도 알려 주지 않는다. 건수를 같이 적으면 관리자가
    표를 스크롤하기 전에 그날의 모양을 먼저 본다.
    """
    row: list[Any] = [Cell("상태별 건수", palette.label)]
    for state in STATE_SEVERITY_ORDER:
        row.append(Cell(f"{state_label(state)}  {counts.get(state, 0)}건", palette.state_style(state)))
    return row


def status_sheet(palette: Palette, report_date: str, classroom: str, events: list[Event]) -> SheetSpec:
    """학생별 **최신** 상태 스냅샷. 지금 누가 자리에 없는지를 한 화면에서 본다."""
    latest: dict[str, Event] = {}
    for event in events:
        latest[event.student_id or event.student_name] = event

    rows, merges, heights = title_block(
        palette,
        "학생 현황",
        f"{classroom} · {report_date} · 관측된 학생 {len(latest)}명 · 생성 {generated_stamp()}",
        5,
    )
    header = ["좌석번호", "학생명", "학생 상태", "최근 관측(KST)", "교시"]
    rows.append([Cell(name, palette.header) for name in header])
    heights[len(rows)] = 22
    header_row = len(rows)

    ordered = sorted(latest.values(), key=lambda item: (item.seat_number, item.student_name))
    for index, event in enumerate(ordered):
        style = palette.row_styles(index % 2 == 1)
        rows.append(
            [
                Cell(event.seat_number, style["center"]),
                Cell(event.student_name, style["text"]),
                Cell(state_label(event.student_state), palette.state_style(event.student_state)),
                Cell(to_kst_text(event.observed_at), style["center"]),
                Cell(event.period, style["center"]),
            ]
        )

    return SheetSpec(
        rows=rows,
        col_widths=[12, 14, 16, 22, 12],
        freeze_row=header_row,
        autofilter_ref=f"A{header_row}:E{max(len(rows), header_row)}",
        merge_refs=merges,
        row_heights=heights,
    )


def event_sheet(palette: Palette, report_date: str, classroom: str, events: list[Event]) -> SheetSpec:
    """상태 변화 기록. 이 워크북에서 가장 길고 가장 많이 읽히는 표다."""
    counts = Counter(event.student_state for event in events)

    rows, merges, heights = title_block(
        palette,
        "상태 변화 기록",
        f"{classroom} · {report_date} · 총 {len(events)}건 · 생성 {generated_stamp()}",
        6,
    )
    rows.append(legend_row(palette, counts))
    heights[len(rows)] = 22
    rows.append([])
    heights[len(rows)] = 6

    header = ["순번", "관측시각(KST)", "교시", "좌석번호", "학생명", "학생 상태"]
    rows.append([Cell(name, palette.header) for name in header])
    heights[len(rows)] = 22
    header_row = len(rows)

    # 시간순으로 세운다. 기록이 들어온 순서가 아니라 일어난 순서로 읽혀야 로그다.
    ordered = sorted(events, key=lambda item: (item.observed_at, item.period, item.seat_number))
    for index, event in enumerate(ordered, start=1):
        style = palette.row_styles(index % 2 == 0)
        rows.append(
            [
                Cell(index, style["number"]),
                Cell(to_kst_text(event.observed_at), style["center"]),
                Cell(event.period, style["center"]),
                Cell(event.seat_number, style["center"]),
                Cell(event.student_name, style["text"]),
                Cell(state_label(event.student_state), palette.state_style(event.student_state)),
            ]
        )

    last_row = len(rows)
    return SheetSpec(
        rows=rows,
        col_widths=[8, 22, 12, 12, 14, 16],
        freeze_row=header_row,
        autofilter_ref=f"A{header_row}:F{max(last_row, header_row)}",
        merge_refs=merges,
        row_heights=heights,
    )


def chart_sheet(palette: Palette, report_date: str, classroom: str, events: list[Event]) -> SheetSpec:
    """차트 두 개가 앉는 시트.

    **차트 앵커가 4행과 22행에서 시작한다.** 위쪽 줄 수를 바꾸면 그림이 글자를 덮는다.
    """
    leave_total = sum(1 for event in events if event.student_state in LEAVE_STATES)
    rate = round(leave_total / len(events) * 100, 2) if events else 0

    rows, merges, heights = title_block(
        palette,
        "일일 리포트",
        f"{classroom} · {report_date} · 상태 변화 {len(events)}건 · "
        f"자리 이탈 {leave_total}건 · 이탈률 {rate}% · 생성 {generated_stamp()}",
        8,
    )
    # title_block이 3행을 빈 줄로 두고 간다. 그 자리에 구역 이름표를 얹는다.
    rows[2] = [
        Cell("상태별 종합", palette.section),
        "",
        "",
        "",
        Cell("학생별 자리 이탈 정도", palette.section),
    ]
    merges.extend(["A3:D3", "E3:H3"])
    heights[3] = 20

    return SheetSpec(
        rows=rows,
        col_widths=[16, 16, 16, 16, 16, 16, 16, 16],
        merge_refs=merges,
        row_heights=heights,
        drawing_rel_id="rId1",
    )


def report_data_rows(events: list[Event]) -> list[list[Any]]:
    """숨은 차트 원천. **줄 배치를 바꾸면 차트가 깨진다.**"""
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


def build_sheets(
    palette: Palette, report_date: str, classroom: str, events: list[Event]
) -> dict[str, SheetSpec]:
    return {
        "학생 현황": status_sheet(palette, report_date, classroom, events),
        "상태 변화 기록": event_sheet(palette, report_date, classroom, events),
        "일일 리포트": chart_sheet(palette, report_date, classroom, events),
        "리포트 데이터": SheetSpec(rows=report_data_rows(events), col_widths=[24, 16, 16]),
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
        '<xdr:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/></xdr:xfrm>'
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


def bar_chart_xml(title: str, category_ref: str, value_ref: str, bar_rgb: str) -> str:
    """막대 차트 하나. 색을 인자로 받아 두 차트가 서로 다른 뜻임을 드러낸다."""
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<c:chart>"
        "<c:title><c:tx><c:rich><a:bodyPr/><a:lstStyle/><a:p>"
        '<a:pPr><a:defRPr sz="1200" b="1"><a:solidFill><a:srgbClr val="102A43"/></a:solidFill>'
        '<a:latin typeface="맑은 고딕"/></a:defRPr></a:pPr>'
        f"<a:r><a:t>{escape(title)}</a:t></a:r>"
        "</a:p></c:rich></c:tx><c:overlay val=\"0\"/></c:title>"
        '<c:autoTitleDeleted val="0"/>'
        "<c:plotArea><c:layout/>"
        '<c:barChart><c:barDir val="col"/><c:grouping val="clustered"/><c:varyColors val="0"/>'
        '<c:ser><c:idx val="0"/><c:order val="0"/>'
        f'<c:spPr><a:solidFill><a:srgbClr val="{bar_rgb}"/></a:solidFill></c:spPr>'
        f'<c:cat><c:strRef><c:f>{escape(category_ref)}</c:f></c:strRef></c:cat>'
        f'<c:val><c:numRef><c:f>{escape(value_ref)}</c:f></c:numRef></c:val>'
        '</c:ser><c:gapWidth val="60"/><c:axId val="123456"/><c:axId val="654321"/></c:barChart>'
        '<c:catAx><c:axId val="123456"/><c:scaling><c:orientation val="minMax"/></c:scaling>'
        '<c:delete val="0"/><c:axPos val="b"/><c:tickLblPos val="nextTo"/>'
        '<c:spPr><a:ln><a:solidFill><a:srgbClr val="B7C2D0"/></a:solidFill></a:ln></c:spPr>'
        '<c:txPr><a:bodyPr rot="-2700000"/><a:lstStyle/><a:p><a:pPr><a:defRPr sz="900">'
        '<a:solidFill><a:srgbClr val="243B53"/></a:solidFill><a:latin typeface="맑은 고딕"/>'
        "</a:defRPr></a:pPr><a:endParaRPr lang=\"ko-KR\"/></a:p></c:txPr>"
        '<c:crossAx val="654321"/></c:catAx>'
        '<c:valAx><c:axId val="654321"/><c:scaling><c:orientation val="minMax"/></c:scaling>'
        '<c:delete val="0"/><c:axPos val="l"/>'
        '<c:majorGridlines><c:spPr><a:ln><a:solidFill><a:srgbClr val="E6EAF0"/></a:solidFill></a:ln>'
        "</c:spPr></c:majorGridlines>"
        '<c:tickLblPos val="nextTo"/>'
        '<c:txPr><a:bodyPr/><a:lstStyle/><a:p><a:pPr><a:defRPr sz="900">'
        '<a:solidFill><a:srgbClr val="627D98"/></a:solidFill><a:latin typeface="맑은 고딕"/>'
        "</a:defRPr></a:pPr><a:endParaRPr lang=\"ko-KR\"/></a:p></c:txPr>"
        '<c:crossAx val="123456"/></c:valAx>'
        '<c:spPr><a:noFill/><a:ln><a:noFill/></a:ln></c:spPr>'
        "</c:plotArea>"
        # 계열이 하나뿐이라 범례는 같은 말을 한 번 더 한다. 지워서 그림에 자리를 준다.
        '<c:plotVisOnly val="1"/><c:dispBlanksAs val="gap"/>'
        "</c:chart>"
        "</c:chartSpace>"
    )


def write_xlsx(path: Path, sheets: dict[str, SheetSpec], styles: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet_names = list(sheets)
    report_sheet_index = sheet_names.index("일일 리포트") + 1

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types_xml(len(sheet_names)))
        archive.writestr("_rels/.rels", root_rels_xml())
        archive.writestr("xl/workbook.xml", workbook_xml(sheet_names))
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml(len(sheet_names)))
        archive.writestr("xl/styles.xml", styles)
        archive.writestr(f"xl/worksheets/_rels/sheet{report_sheet_index}.xml.rels", worksheet_rels_xml())
        archive.writestr("xl/drawings/drawing1.xml", drawing_xml())
        archive.writestr("xl/drawings/_rels/drawing1.xml.rels", drawing_rels_xml())
        archive.writestr(
            "xl/charts/chart1.xml",
            bar_chart_xml(
                "상태별 종합",
                sheet_ref("리포트 데이터", "A$2:$A$6"),
                sheet_ref("리포트 데이터", "B$2:$B$6"),
                "1F3A5F",
            ),
        )
        student_rows = max(len(sheets["리포트 데이터"].rows) - 8, 1)
        last_student_row = 8 + student_rows
        archive.writestr(
            "xl/charts/chart2.xml",
            bar_chart_xml(
                "학생별 자리 이탈 정도",
                sheet_ref("리포트 데이터", f"A$9:$A${last_student_row}"),
                sheet_ref("리포트 데이터", f"B$9:$B${last_student_row}"),
                "9E2B1E",
            ),
        )

        for idx, name in enumerate(sheet_names, start=1):
            spec = sheets[name]
            archive.writestr(
                posixpath.join("xl", "worksheets", f"sheet{idx}.xml"),
                sheet_xml(
                    spec.rows,
                    col_widths=spec.col_widths,
                    freeze_row=spec.freeze_row,
                    autofilter_ref=spec.autofilter_ref,
                    merge_refs=spec.merge_refs,
                    drawing_rel_id=spec.drawing_rel_id,
                    row_heights=spec.row_heights,
                ),
            )


def safe_filename(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣_.-]+", "_", value).strip("_") or "classroom"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    # 기본값도 KST로 잡는다. 컨테이너 TZ를 따르면 자정 근처에서 전날 것이 만들어진다.
    parser.add_argument("--date", default=datetime.now(KST).date().isoformat())
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

    book = StyleBook()
    palette = Palette(book)
    sheets = build_sheets(palette, args.date, args.classroom, events)
    output = args.out or args.output_dir / f"study_status_{args.date}_{safe_filename(args.classroom)}.xlsx"
    write_xlsx(output, sheets, book.to_xml())
    print(output)


if __name__ == "__main__":
    main()
