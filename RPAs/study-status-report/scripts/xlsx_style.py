"""워크북의 **서식 어휘**를 한곳에 모은다.

**왜 별도 모듈인가.** 일일과 주간이 각자 셀을 조립하면 같은 '학생 상태'가 두 워크북에서
다른 색으로 나온다. 받는 사람은 그 차이를 의미로 읽는다 — 색이 다르면 다른 뜻인 줄 안다.
그래서 색·글꼴·테두리·숫자 서식을 여기서만 정하고, 두 스크립트는 이름으로 가져다 쓴다.

**왜 스타일 인덱스를 상수로 박지 않는가.** xlsx의 ``cellXfs``는 배열이고 셀은 그
**인덱스**를 가리킨다. 상수로 두면 중간에 하나 끼워 넣는 순간 이후 전부가 한 칸씩 밀려서,
파일은 멀쩡히 열리는데 색만 조용히 어긋난다. 그래서 :class:`StyleBook` 이 등록하면서
인덱스를 돌려주고 호출부는 이름만 쓴다.

**pip을 쓰지 않는다.** 실행기 컨테이너에 openpyxl을 넣을 수 없어(파이썬 패키지 설치 불가)
OOXML을 직접 쓴다. 스키마의 요소 순서 같은 제약이 이 파일에 그대로 드러나는 이유다.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

KST = timezone(timedelta(hours=9))

# charset 129는 한글이다. 지정해 두면 한글 글꼴이 없는 환경에서 대체 글꼴을 덜 헤맨다.
BODY_FONT = "맑은 고딕"

# 구조색. 강조는 남색 하나로 통일하고 나머지는 잉크의 농담으로만 층을 나눈다.
INK = "FF102A43"
INK_BODY = "FF243B53"
INK_MUTED = "FF627D98"
LINE = "FFDCE2EA"
LINE_STRONG = "FFB7C2D0"
BAND = "FFF7F9FB"
HEAD_BG = "FF1F3A5F"
HEAD_FG = "FFFFFFFF"
CARD_BG = "FFEDF2F9"

# **상태색은 심각도 한 줄이다.** 임의의 다섯 색이 아니라 초록에서 빨강으로 가는 램프고,
# '판단 보류'만 그 줄에서 빼내 무채색으로 둔다. 판단하지 못한 것은 경고가 아니라 정보가
# 없는 것이라, 경고색을 주면 관리자가 대응할 일이 있는 줄로 읽는다.
STATE_FILL_FONT: dict[str, tuple[str, str]] = {
    "ABSENT": ("FFF7E3E0", "FF9E2B1E"),
    "WRONG_SEAT": ("FFFBEBDC", "FFA85B12"),
    "IN_CLASSROOM": ("FFFBF3DC", "FF836318"),
    "UNKNOWN": ("FFECEFF3", "FF5A6678"),
    "PRESENT": ("FFE3F0E9", "FF2B6B4F"),
}

# 범례와 요약을 이 순서로 늘어놓는다. 관리자가 먼저 봐야 할 상태가 왼쪽에 온다.
STATE_SEVERITY_ORDER = ("ABSENT", "WRONG_SEAT", "IN_CLASSROOM", "UNKNOWN", "PRESENT")


def escape(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def col_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def to_kst_text(value: str, *, with_date: bool = True) -> str:
    """ISO 8601 관측시각을 사람이 읽는 KST 문자열로 바꾼다.

    원본은 ``2026-08-27T00:40:17.619000Z`` 같은 UTC다. 그대로 두면 09시 수업이 00시로
    보여서, 보고서를 받은 사람이 시간표와 맞춰 보려면 매번 9를 더해야 한다.

    **해석하지 못하면 원본을 그대로 돌려준다.** 읽을 수 없는 값을 빈칸으로 만들면 기록이
    없었던 것과 구분되지 않는다.
    """
    raw = (value or "").strip()
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    local = parsed.astimezone(KST)
    return local.strftime("%Y-%m-%d %H:%M:%S" if with_date else "%H:%M:%S")


@dataclass(frozen=True)
class Cell:
    """값과 스타일 인덱스 한 쌍. 스타일 0은 기본 서식이다."""

    value: Any = ""
    style: int = 0


class StyleBook:
    """``styles.xml``을 조립하면서 등록한 서식의 인덱스를 돌려준다.

    같은 정의는 두 번 넣지 않는다. 색 조합은 표마다 반복되는데 중복을 그대로 쌓으면
    Excel의 서식 목록이 수백 개로 불어난다.
    """

    def __init__(self) -> None:
        self._numfmts: dict[str, int] = {}
        self._fonts: list[str] = []
        # **0번은 none, 1번은 gray125여야 한다.** OOXML이 정한 자리라, 다른 것을 넣으면
        # Excel이 채우기 인덱스를 통째로 어긋나게 읽는다.
        self._fills: list[str] = [
            '<fill><patternFill patternType="none"/></fill>',
            '<fill><patternFill patternType="gray125"/></fill>',
        ]
        self._borders: list[str] = ["<border><left/><right/><top/><bottom/><diagonal/></border>"]
        self._xfs: list[str] = []
        self.font()  # 0번 기본 글꼴
        self.xf()  # 0번 기본 서식

    @staticmethod
    def _put(store: list[str], xml: str) -> int:
        try:
            return store.index(xml)
        except ValueError:
            store.append(xml)
            return len(store) - 1

    def font(
        self,
        *,
        size: float = 10,
        color: str = INK_BODY,
        bold: bool = False,
        italic: bool = False,
        name: str = BODY_FONT,
    ) -> int:
        # CT_Font는 요소 순서를 지킨다. b -> i -> sz -> color -> name 차례다.
        parts: list[str] = []
        if bold:
            parts.append("<b/>")
        if italic:
            parts.append("<i/>")
        parts.append(f'<sz val="{size}"/>')
        parts.append(f'<color rgb="{color}"/>')
        parts.append(f'<name val="{escape(name)}"/>')
        parts.append('<family val="2"/><charset val="129"/>')
        return self._put(self._fonts, f"<font>{''.join(parts)}</font>")

    def fill(self, rgb: str | None) -> int:
        if rgb is None:
            return 0
        xml = (
            '<fill><patternFill patternType="solid">'
            f'<fgColor rgb="{rgb}"/><bgColor indexed="64"/>'
            "</patternFill></fill>"
        )
        return self._put(self._fills, xml)

    def border(
        self,
        *,
        color: str = LINE,
        sides: str = "",
        style: str = "thin",
        bottom_color: str | None = None,
    ) -> int:
        """``sides``는 ``"lrtb"`` 중 그릴 변만 담는다. 빈 문자열이면 테두리가 없다."""
        if not sides:
            return 0
        edges: list[str] = []
        for key, tag in (("l", "left"), ("r", "right"), ("t", "top"), ("b", "bottom")):
            if key in sides:
                edge_color = bottom_color if (tag == "bottom" and bottom_color) else color
                edges.append(f'<{tag} style="{style}"><color rgb="{edge_color}"/></{tag}>')
            else:
                edges.append(f"<{tag}/>")
        return self._put(self._borders, f"<border>{''.join(edges)}</border>")

    def numfmt(self, code: str) -> int:
        # 사용자 정의 서식 id는 164부터다. 그 아래는 Excel이 예약해 뒀다.
        if code not in self._numfmts:
            self._numfmts[code] = 164 + len(self._numfmts)
        return self._numfmts[code]

    def xf(
        self,
        *,
        font: int = 0,
        fill: int = 0,
        border: int = 0,
        numfmt: int = 0,
        horizontal: str | None = None,
        vertical: str = "center",
        wrap: bool = False,
        indent: int = 0,
    ) -> int:
        align_bits = []
        if horizontal:
            align_bits.append(f'horizontal="{horizontal}"')
        align_bits.append(f'vertical="{vertical}"')
        if wrap:
            align_bits.append('wrapText="1"')
        if indent:
            align_bits.append(f'indent="{indent}"')
        alignment = "<alignment " + " ".join(align_bits) + "/>"
        numfmt_attr = ' applyNumberFormat="1"' if numfmt else ""
        xml = (
            f'<xf numFmtId="{numfmt}" fontId="{font}" fillId="{fill}" borderId="{border}" xfId="0"'
            ' applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"'
            f"{numfmt_attr}>{alignment}</xf>"
        )
        return self._put(self._xfs, xml)

    def to_xml(self) -> str:
        numfmt_xml = ""
        if self._numfmts:
            items = "".join(
                f'<numFmt numFmtId="{fmt_id}" formatCode="{escape(code)}"/>'
                for code, fmt_id in sorted(self._numfmts.items(), key=lambda kv: kv[1])
            )
            numfmt_xml = f'<numFmts count="{len(self._numfmts)}">{items}</numFmts>'
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"{numfmt_xml}"
            f'<fonts count="{len(self._fonts)}">{"".join(self._fonts)}</fonts>'
            f'<fills count="{len(self._fills)}">{"".join(self._fills)}</fills>'
            f'<borders count="{len(self._borders)}">{"".join(self._borders)}</borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            f'<cellXfs count="{len(self._xfs)}">{"".join(self._xfs)}</cellXfs>'
            '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
            "</styleSheet>"
        )


class Palette:
    """워크북이 쓰는 서식을 이름으로 들고 있는다.

    호출부는 ``palette.header`` 처럼 쓰고 인덱스는 모른다. :class:`StyleBook` 과 짝이다.
    """

    def __init__(self, book: StyleBook) -> None:
        self.book = book
        count_fmt = book.numfmt("#,##0")
        pct_fmt = book.numfmt('0.00"%"')

        cell_border = book.border(sides="lrtb")
        head_border = book.border(sides="lrtb", color=HEAD_BG)
        rule = book.border(sides="b", color=LINE_STRONG)

        f_title = book.font(size=16, color=INK, bold=True)
        f_sub = book.font(size=9, color=INK_MUTED)
        f_section = book.font(size=12, color=INK, bold=True)
        f_head = book.font(size=10, color=HEAD_FG, bold=True)
        f_body = book.font(size=10, color=INK_BODY)
        f_body_bold = book.font(size=10, color=INK_BODY, bold=True)
        f_muted = book.font(size=10, color=INK_MUTED, italic=True)
        f_label = book.font(size=10, color=INK_MUTED, bold=True)
        f_metric = book.font(size=18, color=INK, bold=True)

        self.title = book.xf(font=f_title, horizontal="left")
        self.subtitle = book.xf(font=f_sub, horizontal="left")
        self.section = book.xf(font=f_section, border=rule, horizontal="left")
        self.note = book.xf(font=f_sub, horizontal="left", wrap=True)
        self.blank = 0

        self.header = book.xf(
            font=f_head,
            fill=book.fill(HEAD_BG),
            border=head_border,
            horizontal="center",
            wrap=True,
        )

        # 본문은 줄무늬 유무로 두 벌씩 만든다. 열이 많은 표에서 눈이 줄을 놓치지 않게 한다.
        band = book.fill(BAND)
        self.text = book.xf(font=f_body, border=cell_border, horizontal="left", indent=1)
        self.text_band = book.xf(
            font=f_body, fill=band, border=cell_border, horizontal="left", indent=1
        )
        self.center = book.xf(font=f_body, border=cell_border, horizontal="center")
        self.center_band = book.xf(font=f_body, fill=band, border=cell_border, horizontal="center")
        self.number = book.xf(
            font=f_body, border=cell_border, numfmt=count_fmt, horizontal="right", indent=1
        )
        self.number_band = book.xf(
            font=f_body, fill=band, border=cell_border, numfmt=count_fmt, horizontal="right", indent=1
        )
        self.percent = book.xf(
            font=f_body_bold, border=cell_border, numfmt=pct_fmt, horizontal="right", indent=1
        )
        self.percent_band = book.xf(
            font=f_body_bold,
            fill=band,
            border=cell_border,
            numfmt=pct_fmt,
            horizontal="right",
            indent=1,
        )
        # '기록 없음'은 0과 다르다. 숫자 자리에 들어가는 문자열이라 흐리게 눕혀 구분한다.
        self.muted = book.xf(font=f_muted, border=cell_border, horizontal="right", indent=1)
        self.muted_band = book.xf(
            font=f_muted, fill=band, border=cell_border, horizontal="right", indent=1
        )

        self.label = book.xf(font=f_label, horizontal="left")
        self.value = book.xf(font=f_body, horizontal="left", indent=1)
        self.value_strong = book.xf(font=f_body_bold, horizontal="left", indent=1)
        self.metric = book.xf(
            font=f_metric, fill=book.fill(CARD_BG), border=cell_border, horizontal="center"
        )
        self.metric_label = book.xf(
            font=f_sub, fill=book.fill(CARD_BG), border=cell_border, horizontal="center"
        )

        # 상태 배지. 가운데 정렬한 굵은 글씨에 옅은 바탕을 깔아 셀 자체가 배지처럼 보이게 한다.
        self.state: dict[str, int] = {}
        for state, (fill_rgb, font_rgb) in STATE_FILL_FONT.items():
            self.state[state] = book.xf(
                font=book.font(size=10, color=font_rgb, bold=True),
                fill=book.fill(fill_rgb),
                border=cell_border,
                horizontal="center",
            )

    def state_style(self, state: str) -> int:
        """모르는 상태는 색을 만들지 않고 기본 서식으로 둔다.

        FastAPI가 새 상태를 추가해도 워크북은 깨지지 않아야 하고, 무엇보다 뜻을 모르는
        값에 임의의 색을 붙이면 그 색이 의미인 줄로 읽힌다.
        """
        return self.state.get(state, self.center)

    def row_styles(self, banded: bool) -> dict[str, int]:
        """줄무늬 여부에 맞는 본문 서식 묶음."""
        if banded:
            return {
                "text": self.text_band,
                "center": self.center_band,
                "number": self.number_band,
                "percent": self.percent_band,
                "muted": self.muted_band,
            }
        return {
            "text": self.text,
            "center": self.center,
            "number": self.number,
            "percent": self.percent,
            "muted": self.muted,
        }


def sheet_xml(
    rows: list[list[Any]],
    *,
    col_widths: list[float] | None = None,
    freeze_row: int | None = None,
    autofilter_ref: str | None = None,
    merge_refs: list[str] | None = None,
    drawing_rel_id: str | None = None,
    row_heights: dict[int, float] | None = None,
    default_width: float = 16,
    column_count: int = 8,
) -> str:
    """스타일이 붙은 워크시트 XML.

    **요소 순서를 지켜야 한다.** OOXML의 워크시트는 시퀀스라
    ``sheetViews -> sheetFormatPr -> cols -> sheetData -> autoFilter -> mergeCells -> drawing``
    순서를 어기면 Excel이 "복구하시겠습니까" 대화상자를 띄운다. 아래 조립 순서가 그 규칙이다.
    """
    row_heights = row_heights or {}
    row_xml: list[str] = []
    for row_index, row in enumerate(rows, start=1):
        cells: list[str] = []
        for col_index, item in enumerate(row, start=1):
            cell = item if isinstance(item, Cell) else Cell(item)
            ref = f"{col_name(col_index)}{row_index}"
            style_attr = f' s="{cell.style}"' if cell.style else ""
            value = cell.value
            if isinstance(value, int | float) and not isinstance(value, bool):
                cells.append(f'<c r="{ref}"{style_attr}><v>{value}</v></c>')
            elif value == "" and not cell.style:
                # 서식도 값도 없는 칸은 아예 적지 않는다. 파일이 작아지고 Excel도 빨라진다.
                continue
            else:
                cells.append(
                    f'<c r="{ref}"{style_attr} t="inlineStr"><is><t xml:space="preserve">'
                    f"{escape(value)}</t></is></c>"
                )
        height = row_heights.get(row_index)
        height_attr = f' ht="{height}" customHeight="1"' if height else ""
        row_xml.append(f'<row r="{row_index}"{height_attr}>{"".join(cells)}</row>')

    if col_widths:
        cols = "".join(
            f'<col min="{i}" max="{i}" width="{width}" customWidth="1"/>'
            for i, width in enumerate(col_widths, start=1)
        )
    else:
        cols = f'<col min="1" max="{column_count}" width="{default_width}" customWidth="1"/>'

    view_inner = ""
    if freeze_row:
        view_inner = (
            f'<pane ySplit="{freeze_row}" topLeftCell="A{freeze_row + 1}" '
            'activePane="bottomLeft" state="frozen"/>'
            '<selection pane="bottomLeft"/>'
        )

    autofilter_xml = f'<autoFilter ref="{escape(autofilter_ref)}"/>' if autofilter_ref else ""
    merge_xml = ""
    if merge_refs:
        items = "".join(f'<mergeCell ref="{escape(ref)}"/>' for ref in merge_refs)
        merge_xml = f'<mergeCells count="{len(merge_refs)}">{items}</mergeCells>'
    drawing_xml = f'<drawing r:id="{drawing_rel_id}"/>' if drawing_rel_id else ""

    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        # 격자선을 끈다. 테두리를 직접 그리므로 격자선이 남으면 선이 두 벌로 보인다.
        f'<sheetViews><sheetView showGridLines="0" workbookViewId="0">{view_inner}</sheetView></sheetViews>'
        '<sheetFormatPr defaultRowHeight="17"/>'
        f"<cols>{cols}</cols>"
        f"<sheetData>{''.join(row_xml)}</sheetData>"
        f"{autofilter_xml}{merge_xml}{drawing_xml}"
        "</worksheet>"
    )
