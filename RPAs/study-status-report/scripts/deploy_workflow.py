"""저장소의 워크플로를 돌고 있는 n8n에 반영한다.

**반드시 비활성화 → 갱신 → 활성화 순으로 한다.** 단순 PUT만 하면 5분 간격 트리거는
재등록되는데 **cron 트리거는 그날 몫을 놓친다.** 2026-08-26에 실측했다 — 8/25에는
18:05 일일 리포트가 정상 발화했는데, 8/26에 PUT을 두 번 하고 나니 5분 트리거는 계속
돌면서 일일 리포트만 발화하지 않았다. 비활성화 후 다시 활성화하니 곧바로 발화했다.

**정적 데이터는 그대로 넘긴다.** 거기에 그날 상태 변화 이벤트와 이미 보고한 교시
원장(`reportedPeriods`)이 들어 있다. 빠뜨리면 그날 기록이 사라지고 이미 올린 교시가
다시 올라간다.

    python RPAs/study-status-report/scripts/deploy_workflow.py
    python RPAs/study-status-report/scripts/deploy_workflow.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_FILE = ROOT / "workflows" / "study-status-report.n8n.json"
ENV_FILE = ROOT / ".env"
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


def api(base_url: str, api_key: str, path: str, method: str = "GET", payload: Any = None) -> Any:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    if method == "POST" and data is None:
        data = b"{}"
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=data,
        headers={"X-N8N-API-KEY": api_key, "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:400]
        raise SystemExit(f"n8n API {method} {path} 실패 ({error.code}): {detail}") from None
    return json.loads(body) if body else {}


def find_workflow(base_url: str, api_key: str, name: str) -> dict[str, Any]:
    data = api(base_url, api_key, "/api/v1/workflows?limit=100")
    for workflow in data.get("data", []):
        if workflow.get("name") == name:
            return workflow
    raise SystemExit(f"워크플로를 찾지 못했습니다: {name}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_env_file(ENV_FILE)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default=DEFAULT_WORKFLOW_NAME)
    parser.add_argument("--base-url", default=os.environ.get("N8N_BASE_URL", ""))
    parser.add_argument("--api-key", default=os.environ.get("N8N_API_KEY", ""))
    parser.add_argument("--dry-run", action="store_true", help="바꾸지 않고 차이만 보여준다")
    args = parser.parse_args()

    if not args.base_url or not args.api_key:
        raise SystemExit("N8N_BASE_URL과 N8N_API_KEY가 필요합니다(.env).")

    repo = json.loads(WORKFLOW_FILE.read_text(encoding="utf-8"))
    live = find_workflow(args.base_url, args.api_key, args.name)
    workflow_id = live["id"]

    live_names = {node["name"] for node in live.get("nodes", [])}
    repo_names = {node["name"] for node in repo["nodes"]}
    print(f"워크플로 {args.name} (id={workflow_id}, active={live['active']})")
    print(f"  노드 {len(live_names)} -> {len(repo_names)}")
    if repo_names - live_names:
        print(f"  추가: {sorted(repo_names - live_names)}")
    if live_names - repo_names:
        print(f"  제거: {sorted(live_names - repo_names)}")
    if args.dry_run:
        print("dry-run이라 바꾸지 않았습니다.")
        return 0

    detail = api(args.base_url, args.api_key, f"/api/v1/workflows/{workflow_id}")
    static_data = detail.get("staticData")

    # 순서가 핵심이다. 위 docstring 참고.
    api(args.base_url, args.api_key, f"/api/v1/workflows/{workflow_id}/deactivate", "POST")
    api(
        args.base_url,
        args.api_key,
        f"/api/v1/workflows/{workflow_id}",
        "PUT",
        {
            "name": detail["name"],
            "nodes": repo["nodes"],
            "connections": repo["connections"],
            "settings": repo["settings"],
            "staticData": static_data,
        },
    )
    api(args.base_url, args.api_key, f"/api/v1/workflows/{workflow_id}/activate", "POST")

    after = api(args.base_url, args.api_key, f"/api/v1/workflows/{workflow_id}")
    global_data = (after.get("staticData") or {}).get("global") or {}
    print(f"  반영 완료 · active={after['active']} · 노드 {len(after['nodes'])}")
    print(
        f"  정적 데이터 보존 · events={len(global_data.get('events', []))} "
        f"reportedPeriods={len(global_data.get('reportedPeriods', {}))}"
    )
    if not after["active"]:
        print("  경고: 활성화되지 않았습니다. 편집기에서 확인하세요.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
