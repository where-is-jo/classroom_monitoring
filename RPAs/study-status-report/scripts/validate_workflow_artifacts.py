"""Validate the n8n workflow JSON and generated workbook structure."""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "workflows" / "study-status-report.n8n.json"
WORKBOOK = ROOT / "reports" / "study_status_management_sample.xlsx"
SLACK_UPLOAD_SCRIPT = ROOT / "scripts" / "slack_upload_file.py"

# n8n 표현식에서 다른 노드를 부르는 형태. 이름 문자열이라 편집기에서 노드 이름을
# 바꾸면 조용히 깨진다 — 그래서 여기서 검사한다.
NODE_REFERENCE = re.compile(r"\$\('([^']+)'\)")

# **노드 이름 참조는 여기 적힌 것만 허용한다.** HTTP 응답이 항목의 json을 통째로
# 덮어쓰기 때문에 뒤 노드가 앞 노드 값을 쓰려면 이름으로 거슬러 올라가야 하는데,
# 그 참조는 이름을 바꾸는 순간 말없이 끊긴다. 그래서 워크플로는 필요한 값을
# 실행기에 context로 실어 보내고 응답으로 돌려받아 쓴다. 이름 참조는 실행기를
# 거치지 않는 Build Change Events 한 곳만 남겨 뒀다. 새로 늘리지 않는다.
ALLOWED_NODE_REFERENCES = {"Parse Schedule"}


def validate_workflow() -> None:
    data = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    node_names = {node["name"] for node in data["nodes"]}
    required = {
        "Read Schedule File",
        "Parse Schedule",
        "Get Student States",
        "Build Change Events",
        "Create Workbook",
        "Upload Workbook to Slack",
        "Build Daily Report",
        "Create Daily Workbook",
        "Upload Daily Report to Slack",
        "Schedule OK?",
        "Notify Schedule Failure",
        # 교시 보고를 놓치지 않기 위한 노드들.
        "Mark Period Reported",
        "Missed Period?",
        "Notify Missed Period",
    }
    missing = required - node_names
    if missing:
        raise AssertionError(f"Missing n8n nodes: {sorted(missing)}")
    if not data.get("connections"):
        raise AssertionError("Workflow has no connections")
    workflow_text = WORKFLOW.read_text(encoding="utf-8")
    forbidden = [
        "xoxb-",
        "xapp-",
        "hooks.slack.com/services",
        "REPLACE_WITH_N8N_CREDENTIAL_ID",
        "n8n-nodes-base.slack",
    ]
    leaked = [item for item in forbidden if item in workflow_text]
    if leaked:
        raise AssertionError(f"Workflow contains forbidden Slack value or placeholder: {leaked}")
    removed_event_fields = ['"reason"', '"note"', "state.reason"]
    present_event_fields = [item for item in removed_event_fields if item in workflow_text]
    if present_event_fields:
        raise AssertionError(f"Workflow still depends on removed event fields: {present_event_fields}")
    if not SLACK_UPLOAD_SCRIPT.exists():
        raise AssertionError(f"Slack upload script does not exist: {SLACK_UPLOAD_SCRIPT}")
    validate_node_references(workflow_text, node_names)
    validate_period_report_ledger(data, workflow_text)


def validate_node_references(workflow_text: str, node_names: set[str]) -> None:
    """``$('노드 이름')`` 참조가 실제 노드를 가리키는지 확인한다.

    편집기에서 노드 이름을 바꿔도 n8n은 경고하지 않는다. 참조는 undefined가 되어
    빈 값이 아래로 흐르고, 보고서가 빈 채로 올라가거나 조건이 늘 거짓이 된다.
    실행해 보기 전에 여기서 잡는다.
    """
    referenced = set(NODE_REFERENCE.findall(workflow_text))
    unresolved = sorted(referenced - node_names)
    if unresolved:
        raise AssertionError(
            f"Workflow references nodes that do not exist: {unresolved}. "
            "노드 이름을 바꿨다면 그 이름을 쓰는 Code 노드도 함께 고쳐야 한다."
        )
    extra = sorted(referenced - ALLOWED_NODE_REFERENCES)
    if extra:
        raise AssertionError(
            f"New node-name references added: {extra}. "
            "값이 필요하면 실행기에 context로 실어 보내고 응답에서 되받아 쓴다 "
            "(runner/server.py의 _echo_context). 이름 참조는 늘리지 않는다."
        )


def validate_period_report_ledger(data: dict, workflow_text: str) -> None:
    """교시 보고가 '전송 성공 뒤에 원장에 적힌다'는 구조를 지키는지 확인한다.

    예전에는 '종료 후 5분 안'이라는 시간 창으로 판정해서, 트리거 주기와 창이 같은
    탓에 틱이 한 번만 밀려도 그 교시 보고가 조용히 사라졌다. 지금은 아직 보고하지
    않은 교시를 원장으로 찾고 Slack 전송이 끝난 뒤에 표시한다. 이 연결이 끊기면
    같은 교시를 5분마다 다시 올리게 되므로 구조를 못으로 박아 둔다.
    """
    if "reportedPeriods" not in workflow_text:
        raise AssertionError("Workflow no longer keeps a reported-period ledger (reportedPeriods)")
    downstream = [
        target["node"]
        for branch in data["connections"].get("Upload Workbook to Slack", {}).get("main", [])
        for target in branch
    ]
    if "Mark Period Reported" not in downstream:
        raise AssertionError(
            "'Mark Period Reported' must run after 'Upload Workbook to Slack'; "
            f"found downstream nodes: {downstream}"
        )


def validate_workbook() -> None:
    if not WORKBOOK.exists():
        raise AssertionError(f"Workbook does not exist: {WORKBOOK}")
    with zipfile.ZipFile(WORKBOOK) as archive:
        names = set(archive.namelist())
        required = {
            "[Content_Types].xml",
            "xl/workbook.xml",
            "xl/worksheets/sheet1.xml",
            "xl/worksheets/sheet2.xml",
            "xl/worksheets/sheet3.xml",
            "xl/worksheets/sheet4.xml",
            "xl/worksheets/_rels/sheet3.xml.rels",
            "xl/drawings/drawing1.xml",
            "xl/drawings/_rels/drawing1.xml.rels",
            "xl/charts/chart1.xml",
            "xl/charts/chart2.xml",
        }
        missing = required - names
        if missing:
            raise AssertionError(f"Workbook is missing entries: {sorted(missing)}")
        sheet1 = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
        for text in ("날짜", "강의실", "좌석번호", "학생명", "학생 상태"):
            if text not in sheet1:
                raise AssertionError(f"Workbook sheet1 missing text: {text}")
        for removed in ("상태 판단 근거", "원본 근거 코드"):
            if removed in sheet1:
                raise AssertionError(f"Workbook sheet1 still contains removed column: {removed}")
        sheet2 = archive.read("xl/worksheets/sheet2.xml").decode("utf-8")
        for removed in ("상태 판단 근거", "원본 근거 코드"):
            if removed in sheet2:
                raise AssertionError(f"Workbook sheet2 still contains removed column: {removed}")
        sheet3 = archive.read("xl/worksheets/sheet3.xml").decode("utf-8")
        for text in ("상태별 종합", "학생별 자리 이탈 정도", "일일 리포트"):
            if text not in sheet3:
                raise AssertionError(f"Workbook report sheet missing text: {text}")
        sheet4 = archive.read("xl/worksheets/sheet4.xml").decode("utf-8")
        expected_metrics = (
            "미착석 건수",
            "오착석 건수",
            "좌석 외 위치 건수",
            "판단 보류 건수",
            "정상 착석 복귀 건수",
            "자리 이탈 건수",
            "자리 이탈률(%)",
        )
        for text in expected_metrics:
            if text not in sheet4:
                raise AssertionError(f"Workbook report data sheet missing text: {text}")
        forbidden_metrics = ("총 상태 변화 건수", "영향 학생 수")
        for text in forbidden_metrics:
            if text in sheet4:
                raise AssertionError(f"Workbook report data sheet still contains old metric: {text}")


def main() -> None:
    validate_workflow()
    validate_workbook()
    print("OK: n8n workflow JSON and workbook artifact are valid")


if __name__ == "__main__":
    main()
