"""n8n 워크플로를 켜고 끈다.

개발 중에는 워크플로를 잠깐 멈춰야 할 때가 잦다. 편집기를 열지 않고 터미널에서
바로 끄고 켤 수 있게 한다.

**끄면 수집도 멈춘다.** Slack 전송만 막고 싶다면 워크플로는 켜 둔 채 실행기의
``RPA_DRY_RUN=true``를 쓴다 — 관리 문서는 그대로 만들어지고 Slack 전송만 건너뛴다.

    python RPAs/study-status-report/scripts/toggle_workflow.py --status
    python RPAs/study-status-report/scripts/toggle_workflow.py --off
    python RPAs/study-status-report/scripts/toggle_workflow.py --on
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
DEFAULT_WORKFLOW_NAME = "study-status-report"


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def api(base_url: str, api_key: str, path: str, method: str = "GET") -> Any:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=b"{}" if method == "POST" else None,
        headers={"X-N8N-API-KEY": api_key, "Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8")
    return json.loads(body) if body else {}


def find_workflow(base_url: str, api_key: str, name: str) -> dict[str, Any]:
    data = api(base_url, api_key, "/api/v1/workflows?limit=100")
    for workflow in data.get("data", []):
        if workflow.get("name") == name:
            return workflow
    raise SystemExit(f"워크플로를 찾지 못했습니다: {name}")


def parse_args() -> argparse.Namespace:
    load_env_file(ENV_FILE)
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--on", action="store_true", help="워크플로를 켠다")
    action.add_argument("--off", action="store_true", help="워크플로를 끈다")
    action.add_argument("--status", action="store_true", help="현재 상태만 본다")
    parser.add_argument("--name", default=os.environ.get("N8N_WORKFLOW_NAME", DEFAULT_WORKFLOW_NAME))
    parser.add_argument("--base-url", default=os.environ.get("N8N_BASE_URL", ""))
    parser.add_argument("--api-key", default=os.environ.get("N8N_API_KEY", ""))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.base_url:
        raise SystemExit("N8N_BASE_URL이 필요합니다 (.env 또는 --base-url)")
    if not args.api_key:
        raise SystemExit("N8N_API_KEY가 필요합니다 (.env 또는 --api-key)")

    workflow = find_workflow(args.base_url, args.api_key, args.name)
    workflow_id = workflow["id"]
    active = bool(workflow.get("active"))

    if args.status:
        print(f"{args.name}: {'켜짐' if active else '꺼짐'} (id={workflow_id})")
        return

    want_on = bool(args.on)
    if want_on == active:
        print(f"{args.name}: 이미 {'켜짐' if active else '꺼짐'} 상태입니다. 그대로 둡니다.")
        return

    endpoint = "activate" if want_on else "deactivate"
    api(args.base_url, args.api_key, f"/api/v1/workflows/{workflow_id}/{endpoint}", method="POST")
    print(f"{args.name}: {'꺼짐 -> 켜짐' if want_on else '켜짐 -> 꺼짐'}")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"n8n API 오류 {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"n8n에 연결하지 못했습니다: {exc.reason}") from exc
