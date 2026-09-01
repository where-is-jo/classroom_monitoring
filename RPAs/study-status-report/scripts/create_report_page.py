"""자습 현황을 **파일 하나로 완결된 HTML**로 만든다. 검색·필터가 된다.

**왜 .xlsx 옆에 이걸 두나.** 관리자가 엑셀을 열어 필터를 걸어야 "누가 몇 번 비웠나"를
볼 수 있었다. 브라우저로 바로 열어 이름을 치면 나오는 쪽이 빠르다. 엑셀은 그대로
둔다 — 가공해서 다시 쓰는 사람이 있다.

**파일 하나로 끝난다.** CSS와 스크립트를 안에 넣고 데이터도 함께 묻는다. 서버가
없어도, 메일에 첨부해 받아도 열린다. 실행기 컨테이너에 pip이 없어서 템플릿
라이브러리를 쓸 수 없기도 하다.

**학생 실명과 좌석이 들어간다.** `.xlsx`와 같은 등급이라 `reports/`에 두고
`.gitignore`로 막는다.

    python RPAs/study-status-report/scripts/create_report_page.py \\
      --date 2026-08-27 --classroom "4A 강의실" --events-base64 ... --out ...
    python RPAs/study-status-report/scripts/create_report_page.py \\
      --from 2026-08-24 --to 2026-08-28 --classroom "4A 강의실" --out ...
"""

from __future__ import annotations

import argparse
import html
import json
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from create_management_workbook import (
    LEAVE_STATES,
    STATE_LABELS,
    Event,
    load_events_base64,
    parse_events,
    safe_filename,
)

DEFAULT_DATA_DIR = Path("RPAs/study-status-report/data")
DEFAULT_OUTPUT_DIR = Path("RPAs/study-status-report/reports")
WEEKDAY_LABELS = ("월", "화", "수", "목", "금", "토", "일")
# 화면에서 상태를 색으로 가른다. 이탈 계열은 붉은 쪽, 착석은 초록 쪽이다.
STATE_ORDER = ("ABSENT", "WRONG_SEAT", "IN_CLASSROOM", "UNKNOWN", "PRESENT")


def leave_rate(leave_count: int, total: int) -> float:
    """일일·주간 워크북과 같은 식이어야 숫자가 어긋나지 않는다."""
    return round(leave_count / total * 100, 2) if total else 0.0


def student_key(event: Event) -> str:
    return event.student_name or event.student_id or "미확인"


def load_range(data_dir: Path, start: date, end: date, classroom: str) -> tuple[dict[str, list[Event]], list[str]]:
    """기간 안의 일자별 이벤트를 읽는다. 파일이 없는 날은 '기록 없음'으로 남긴다."""
    by_day: dict[str, list[Event]] = {}
    missing: list[str] = []
    for offset in range((end - start).days + 1):
        day = (start + timedelta(days=offset)).isoformat()
        path = data_dir / f"events_{day}_{safe_filename(classroom)}.json"
        if not path.is_file():
            missing.append(day)
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        events = raw.get("events") if isinstance(raw, dict) else raw
        by_day[day] = parse_events(events if isinstance(events, list) else [])
    return by_day, missing


def build_rows(by_day: dict[str, list[Event]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for day in sorted(by_day):
        for event in by_day[day]:
            rows.append(
                {
                    "day": day,
                    "period": event.period,
                    "at": event.observed_at,
                    "seat": event.seat_number,
                    "name": student_key(event),
                    "state": event.student_state,
                    "label": STATE_LABELS.get(event.student_state, event.student_state),
                    "leave": event.student_state in LEAVE_STATES,
                }
            )
    return rows


def build_students(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    totals: Counter[str] = Counter()
    leaves: Counter[str] = Counter()
    seats: dict[str, str] = {}
    days: defaultdict[str, set[str]] = defaultdict(set)
    for row in rows:
        name = row["name"]
        totals[name] += 1
        days[name].add(row["day"])
        if row["seat"]:
            seats.setdefault(name, row["seat"])
        if row["leave"]:
            leaves[name] += 1
    students = [
        {
            "name": name,
            "seat": seats.get(name, ""),
            "total": totals[name],
            "leave": leaves[name],
            "rate": leave_rate(leaves[name], totals[name]),
            "days": len(days[name]),
        }
        for name in totals
    ]
    # 이탈률이 높은 사람이 위에 오게 둔다. 관리자가 먼저 볼 줄이다.
    students.sort(key=lambda item: (-item["rate"], item["name"]))
    return students


PAGE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root {
    --ground: #f4f6f9; --surface: #fff; --line: #dde3ec; --ink: #16202e;
    --ink-2: #5a6678; --accent: #2f5aa8; --leave: #b03a30; --ok: #2b6b4f; --warn: #8a6a1c;
  }
  @media (prefers-color-scheme: dark) {
    :root { --ground:#11151b; --surface:#181e27; --line:#2a3340; --ink:#e5eaf2;
            --ink-2:#a3aebe; --accent:#87a5e6; --leave:#e08a80; --ok:#6fbe98; --warn:#d6ac5c; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--ground); color:var(--ink); font-size:15px; line-height:1.6;
         font-family:"Malgun Gothic","Apple SD Gothic Neo",system-ui,sans-serif; }
  .wrap { max-width:1100px; margin:0 auto; padding:32px 20px 64px; }
  h1 { font-size:24px; margin:0 0 4px; }
  .sub { color:var(--ink-2); margin:0 0 24px; font-size:14px; }
  .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin-bottom:24px; }
  .card { background:var(--surface); border:1px solid var(--line); border-radius:6px; padding:14px 16px; }
  .card b { display:block; font-size:26px; line-height:1.2; font-variant-numeric:tabular-nums; }
  .card span { color:var(--ink-2); font-size:13px; }
  .tools { display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin-bottom:14px; }
  input[type=search] { flex:1 1 220px; min-width:180px; padding:9px 12px; font-size:15px;
    border:1px solid var(--line); border-radius:6px; background:var(--surface); color:var(--ink); }
  .chips { display:flex; flex-wrap:wrap; gap:6px; }
  .chip { padding:7px 12px; font-size:13px; border:1px solid var(--line); border-radius:999px;
    background:var(--surface); color:var(--ink-2); cursor:pointer; }
  .chip[aria-pressed=true] { background:var(--accent); border-color:var(--accent); color:#fff; }
  .tabs { display:flex; gap:4px; border-bottom:1px solid var(--line); margin-bottom:14px; }
  .tab { padding:9px 16px; font-size:14px; border:none; background:none; color:var(--ink-2); cursor:pointer;
    border-bottom:2px solid transparent; }
  .tab[aria-selected=true] { color:var(--ink); border-bottom-color:var(--accent); font-weight:600; }
  .scroll { overflow-x:auto; background:var(--surface); border:1px solid var(--line); border-radius:6px; }
  table { width:100%; border-collapse:collapse; font-size:14px; }
  th, td { text-align:left; padding:9px 14px; border-bottom:1px solid var(--line); white-space:nowrap; }
  th { color:var(--ink-2); font-weight:500; font-size:12.5px; }
  tbody tr:last-child td { border-bottom:none; }
  td.num { text-align:right; font-variant-numeric:tabular-nums; }
  .state { display:inline-block; padding:2px 9px; border-radius:999px; font-size:12.5px; }
  .s-ABSENT,.s-WRONG_SEAT,.s-IN_CLASSROOM { color:var(--leave); border:1px solid var(--leave); }
  .s-PRESENT { color:var(--ok); border:1px solid var(--ok); }
  .s-UNKNOWN { color:var(--warn); border:1px solid var(--warn); }
  .empty { padding:36px; text-align:center; color:var(--ink-2); }
  .note { margin-top:20px; padding:12px 16px; border-left:3px solid var(--warn);
    background:var(--surface); border-radius:0 6px 6px 0; color:var(--ink-2); font-size:13.5px; }
  .count { color:var(--ink-2); font-size:13px; margin:10px 2px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>__HEADING__</h1>
  <p class="sub">__SUBTITLE__</p>
  <div class="cards" id="cards"></div>

  <div class="tabs" role="tablist">
    <button class="tab" role="tab" aria-selected="true" data-view="students">학생별</button>
    <button class="tab" role="tab" aria-selected="false" data-view="events">상태 변화 기록</button>
  </div>

  <div class="tools">
    <input type="search" id="q" placeholder="학생 이름이나 좌석으로 찾기" autocomplete="off">
    <div class="chips" id="chips"></div>
  </div>
  <p class="count" id="count"></p>
  <div class="scroll"><div id="out"></div></div>
  __NOTE__
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
(function () {
  var D = JSON.parse(document.getElementById("data").textContent);
  var view = "students", term = "", state = "ALL";
  var LABEL = D.labels;

  var cards = [
    ["총 상태 변화", D.rows.length],
    ["자리 이탈", D.rows.filter(function (r) { return r.leave; }).length],
    ["자리 이탈률(%)", D.leaveRate],
    ["관측된 학생", D.students.length]
  ];
  document.getElementById("cards").innerHTML = cards.map(function (c) {
    return '<div class="card"><b>' + c[1] + "</b><span>" + c[0] + "</span></div>";
  }).join("");

  var states = ["ALL"].concat(D.order.filter(function (s) {
    return D.rows.some(function (r) { return r.state === s; });
  }));
  document.getElementById("chips").innerHTML = states.map(function (s) {
    return '<button class="chip" data-state="' + s + '" aria-pressed="' + (s === "ALL") + '">' +
      (s === "ALL" ? "전체" : LABEL[s]) + "</button>";
  }).join("");

  function esc(v) {
    return String(v == null ? "" : v).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function hit(text) { return !term || String(text || "").toLowerCase().indexOf(term) !== -1; }

  function render() {
    var out = document.getElementById("out"), count = document.getElementById("count");
    if (view === "students") {
      var list = D.students.filter(function (s) { return hit(s.name) || hit(s.seat); });
      if (state !== "ALL") {
        var names = {};
        D.rows.forEach(function (r) { if (r.state === state) { names[r.name] = 1; } });
        list = list.filter(function (s) { return names[s.name]; });
      }
      count.textContent = list.length + "명";
      out.innerHTML = list.length === 0 ? '<p class="empty">조건에 맞는 학생이 없습니다.</p>' :
        '<table><thead><tr><th>좌석</th><th>학생명</th><th class="num">상태 변화</th>' +
        '<th class="num">자리 이탈</th><th class="num">이탈률(%)</th><th class="num">관측된 날</th></tr></thead><tbody>' +
        list.map(function (s) {
          return "<tr><td>" + esc(s.seat) + "</td><td>" + esc(s.name) + '</td><td class="num">' + s.total +
            '</td><td class="num">' + s.leave + '</td><td class="num">' + s.rate +
            '</td><td class="num">' + s.days + "</td></tr>";
        }).join("") + "</tbody></table>";
      return;
    }
    var rows = D.rows.filter(function (r) {
      return (state === "ALL" || r.state === state) && (hit(r.name) || hit(r.seat));
    });
    count.textContent = rows.length + "건";
    out.innerHTML = rows.length === 0 ? '<p class="empty">조건에 맞는 기록이 없습니다.</p>' :
      "<table><thead><tr>" + (D.multiDay ? "<th>날짜</th>" : "") +
      "<th>교시</th><th>관측시각</th><th>좌석</th><th>학생명</th><th>상태</th></tr></thead><tbody>" +
      rows.map(function (r) {
        return "<tr>" + (D.multiDay ? "<td>" + esc(r.day) + "</td>" : "") +
          "<td>" + esc(r.period) + "</td><td>" + esc(r.at) + "</td><td>" + esc(r.seat) +
          "</td><td>" + esc(r.name) + '</td><td><span class="state s-' + esc(r.state) + '">' +
          esc(r.label) + "</span></td></tr>";
      }).join("") + "</tbody></table>";
  }

  document.getElementById("q").addEventListener("input", function (e) {
    term = e.target.value.trim().toLowerCase(); render();
  });
  document.getElementById("chips").addEventListener("click", function (e) {
    var button = e.target.closest(".chip"); if (!button) { return; }
    state = button.dataset.state;
    Array.prototype.forEach.call(this.children, function (c) {
      c.setAttribute("aria-pressed", String(c === button));
    });
    render();
  });
  Array.prototype.forEach.call(document.querySelectorAll(".tab"), function (tab) {
    tab.addEventListener("click", function () {
      view = tab.dataset.view;
      Array.prototype.forEach.call(document.querySelectorAll(".tab"), function (t) {
        t.setAttribute("aria-selected", String(t === tab));
      });
      render();
    });
  });
  render();
})();
</script>
</body>
</html>
"""


def render_page(
    *, heading: str, subtitle: str, rows: list[dict[str, Any]], multi_day: bool, note: str
) -> str:
    students = build_students(rows)
    leaves = sum(1 for row in rows if row["leave"])
    data = {
        "rows": rows,
        "students": students,
        "leaveRate": leave_rate(leaves, len(rows)),
        "labels": STATE_LABELS,
        "order": list(STATE_ORDER),
        "multiDay": multi_day,
    }
    # </script>가 데이터 안에 나오면 스크립트 블록이 일찍 닫힌다. 학생 이름에 들어갈
    # 일은 없지만 값이 어디서 오는지 보장할 수 없으므로 막아 둔다.
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return (
        PAGE.replace("__TITLE__", html.escape(heading))
        .replace("__HEADING__", html.escape(heading))
        .replace("__SUBTITLE__", html.escape(subtitle))
        .replace("__NOTE__", f'<p class="note">{html.escape(note)}</p>' if note else "")
        .replace("__DATA__", payload)
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--classroom", required=True)
    parser.add_argument("--date", help="일간 보고서로 만든다")
    parser.add_argument("--events-base64", help="--date와 함께 쓴다")
    parser.add_argument("--from", dest="start", help="주간 보고서 시작일")
    parser.add_argument("--to", dest="end", help="주간 보고서 종료일(포함)")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.date:
        if not args.events_base64:
            raise SystemExit("--date에는 --events-base64가 필요합니다.")
        rows = build_rows({args.date: load_events_base64(args.events_base64)})
        heading = f"{args.classroom} 자습 현황"
        weekday = WEEKDAY_LABELS[date.fromisoformat(args.date).weekday()]
        subtitle = f"{args.date} ({weekday})"
        note = ""
        multi_day = False
        default_name = f"study_status_{args.date}_{safe_filename(args.classroom)}.html"
    elif args.start and args.end:
        start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
        if end < start:
            raise SystemExit("--to는 --from보다 빠를 수 없습니다.")
        by_day, missing = load_range(args.data_dir, start, end, args.classroom)
        if not by_day:
            # 빈 보고서를 내보내지 않는다. 받는 사람이 "이탈이 없었다"로 읽는다.
            raise SystemExit(f"{args.start}~{args.end} 사이에 기록이 하나도 없습니다.")
        rows = build_rows(by_day)
        heading = f"{args.classroom} 주간 자습 현황"
        subtitle = f"{args.start} ~ {args.end}"
        note = (
            f"기록이 없는 날: {', '.join(missing)}. 0으로 세지 않았습니다 — "
            "RPA가 멈춰 있던 날과 이탈이 없던 날은 다릅니다."
        ) if missing else ""
        multi_day = True
        default_name = (
            f"weekly_study_status_{args.start}_{args.end}_{safe_filename(args.classroom)}.html"
        )
    else:
        raise SystemExit("--date 또는 --from/--to 중 하나가 필요합니다.")

    out = args.out or args.output_dir / default_name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        render_page(heading=heading, subtitle=subtitle, rows=rows, multi_day=multi_day, note=note),
        encoding="utf-8",
    )
    print(str(out))


if __name__ == "__main__":
    main()
