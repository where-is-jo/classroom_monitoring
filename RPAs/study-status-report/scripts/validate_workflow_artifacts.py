"""Validate the n8n workflow JSON and generated workbook structure."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "workflows" / "study-status-report.n8n.json"
WORKBOOK = ROOT / "reports" / "study_status_management_sample.xlsx"
SLACK_UPLOAD_SCRIPT = ROOT / "scripts" / "slack_upload_file.py"


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
