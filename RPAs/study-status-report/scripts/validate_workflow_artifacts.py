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
        }
        missing = required - names
        if missing:
            raise AssertionError(f"Workbook is missing entries: {sorted(missing)}")
        sheet1 = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
        for text in ("날짜", "강의실", "좌석번호", "학생명", "학생 상태", "상태 판단 근거"):
            if text not in sheet1:
                raise AssertionError(f"Workbook sheet1 missing text: {text}")
        sheet3 = archive.read("xl/worksheets/sheet3.xml").decode("utf-8")
        for text in ("자리 이탈률", "일일 리포트"):
            if text not in sheet3:
                raise AssertionError(f"Workbook report sheet missing text: {text}")


def main() -> None:
    validate_workflow()
    validate_workbook()
    print("OK: n8n workflow JSON and workbook artifact are valid")


if __name__ == "__main__":
    main()
