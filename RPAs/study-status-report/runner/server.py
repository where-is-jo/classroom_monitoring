"""study-status-report 스크립트를 HTTP로 감싸는 실행기.

**왜 이게 있나.** n8n 2.33.5 공식 이미지에는 파이썬이 없고, 패키지 관리자(apk)까지
제거돼 있어 컨테이너 안에서 `python`을 부를 수 없다. 그래서 Execute Command 노드로
스크립트를 직접 실행하지 못한다. 이 서비스는 파이썬이 있는 별도 컨테이너에서 돌면서
기존 스크립트를 그대로 호출하고, n8n은 HTTP Request 노드로 이 서비스를 부른다.

**스크립트를 고쳐 쓰지 않는다.** 검증이 끝난 CLI를 subprocess로 그대로 호출한다 —
로직을 여기로 옮기면 두 벌이 되어 어긋난다.

이 서비스는 compose의 backend network 안에만 열린다. 호스트 포트로 내보내지 않는다.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import sys
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("rpa-runner")

REPO_DIR = Path(os.environ.get("REPO_DIR", "/repo")).resolve()
SCRIPT_DIR = REPO_DIR / "RPAs" / "study-status-report" / "scripts"
LOG_DIR = REPO_DIR / "RPAs" / "study-status-report" / "logs"
# 워크북을 쓰고 Slack에 올리는 대상은 이 디렉터리 안으로 한정한다.
# 저장소 전체를 허용하면 같은 저장소에 있는 .env를 Slack에 올릴 수 있다.
REPORT_DIR = REPO_DIR / "RPAs" / "study-status-report" / "reports"
LISTEN_PORT = int(os.environ.get("RUNNER_PORT", "8099"))
# 워크북 생성은 이벤트가 많으면 수 초가 걸리고, Slack 업로드는 외부 호출이다.
# 무한정 매달리지 않도록 상한을 둔다.
COMMAND_TIMEOUT_SECONDS = float(os.environ.get("RUNNER_TIMEOUT_SECONDS", "120"))

MAX_BODY_BYTES = 8 * 1024 * 1024  # 이벤트 base64가 커질 수 있어 넉넉히 잡는다.

# 같은 사유의 오류 알림을 다시 보내기까지 기다리는 시간. 시간표가 없는 상태는
# 사람이 고칠 때까지 이어지므로, 5분마다 알리면 채널이 묻힌다. 반나절에 한 번이면
# 놓치지 않으면서 쌓이지도 않는다.
DEFAULT_MESSAGE_COOLDOWN_SECONDS = float(os.environ.get("RUNNER_MESSAGE_COOLDOWN_SECONDS", 6 * 60 * 60))
_last_message_at: dict[str, datetime] = {}
_message_lock = Lock()

# **개발용 스위치.** 켜면 Slack 전송만 건너뛴다 — 상태 수집도, 관리 문서 생성도,
# 실행 이력도 그대로 남는다. 워크플로를 통째로 끄면(toggle_workflow.py --off)
# 수집까지 멈추므로, 채널만 조용히 하고 싶을 때 이쪽을 쓴다.
DRY_RUN = os.environ.get("RPA_DRY_RUN", "").strip().lower() in {"1", "true", "yes"}


class RunnerError(Exception):
    """요청이 잘못됐을 때. 스크립트 실행 실패와 구분한다."""


def _resolve_inside(base: Path, raw_path: str) -> Path:
    """``base`` 밖 경로를 쓰지 못하게 막는다.

    경로가 n8n 워크플로에서 넘어온다. **저장소 안이라는 조건만으로는 부족하다** —
    같은 저장소에 Slack 토큰이 든 ``.env``가 있어서, 저장소 전체를 허용하면 그
    파일을 Slack 채널에 올리라고 시킬 수 있다. 그래서 산출물 디렉터리로 좁힌다.
    """
    candidate = Path(raw_path)
    resolved = (candidate if candidate.is_absolute() else REPO_DIR / candidate).resolve()
    if resolved != base and base not in resolved.parents:
        raise RunnerError(f"허용되지 않은 경로입니다: {raw_path}")
    return resolved


def _require(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise RunnerError(f"'{key}'가 필요합니다.")
    return value


def append_run_log(entry: dict[str, object]) -> None:
    """실행 이력을 하루 한 파일에 한 줄씩 남긴다.

    **학생 이름과 토큰은 남기지 않는다**(RPAs/README.md). 대상 식별은 내부 ID인
    student_id만 쓴다. 로그를 남기지 못해도 본 작업을 실패로 만들지는 않는다 —
    이력은 보조 기록이고, 여기서 예외를 올리면 이미 끝난 Slack 전송을 되돌릴 수
    없는데도 호출자가 실패로 보게 된다.
    """
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC)
        record = {"logged_at": stamp.isoformat(), **entry}
        path = LOG_DIR / f"run-{stamp:%Y-%m-%d}.json"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        logger.exception("실행 이력을 남기지 못했습니다. 본 작업은 계속합니다.")


def _state_changes_without_names(events_base64: str) -> list[dict[str, object]]:
    """관리 문서에 담긴 상태 변화에서 개인정보를 뺀 형태로 돌려준다."""
    try:
        decoded = base64.b64decode(events_base64.encode("ascii")).decode("utf-8")
        events = json.loads(decoded)
    except (ValueError, UnicodeDecodeError):
        logger.warning("상태 변화 이벤트를 해석하지 못해 이력에서 건너뜁니다.")
        return []
    if not isinstance(events, list):
        return []
    # student_name과 seat_number는 남기지 않는다 — 이름은 개인정보이고, 좌석은
    # 이름과 붙으면 특정 학생을 지목하게 된다.
    return [
        {
            "period": event.get("period"),
            "observed_at": event.get("observed_at"),
            "student_id": event.get("student_id"),
            "student_state": event.get("student_state"),
        }
        for event in events
        if isinstance(event, dict)
    ]


def _skipped_by_dry_run(action: str) -> dict[str, object] | None:
    """드라이런이면 전송을 건너뛴 결과를 돌려준다. 아니면 None."""
    if not DRY_RUN:
        return None
    logger.info("RPA_DRY_RUN=true — %s 전송을 건너뜁니다.", action)
    return {"ok": True, "returncode": 0, "stdout": f"DRY RUN: {action} 전송을 건너뜀", "stderr": "", "dry_run": True}


def _run(script_name: str, args: list[str]) -> dict[str, object]:
    script_path = SCRIPT_DIR / script_name
    if not script_path.exists():
        raise RunnerError(f"스크립트를 찾을 수 없습니다: {script_path}. 저장소 마운트를 확인하세요.")

    command = [sys.executable, str(script_path), *args]
    # cwd를 저장소 루트로 둔다. 스크립트의 기본 출력 경로가 저장소 기준 상대 경로다.
    completed = subprocess.run(
        command,
        cwd=str(REPO_DIR),
        capture_output=True,
        text=True,
        timeout=COMMAND_TIMEOUT_SECONDS,
        check=False,
    )
    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def handle_workbook(payload: dict[str, object]) -> dict[str, object]:
    """관리 문서(.xlsx)를 만든다."""
    out_path = _resolve_inside(REPORT_DIR, _require(payload, "out"))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    events_base64 = _require(payload, "events_base64")

    args = [
        "--date", _require(payload, "date"),
        "--classroom", _require(payload, "classroom"),
        "--events-base64", events_base64,
        "--out", str(out_path),
    ]
    result = _run("create_management_workbook.py", args)
    result["workbook_path"] = str(out_path)

    changes = _state_changes_without_names(events_base64)
    append_run_log(
        {
            "action": "workbook",
            "ok": result["ok"],
            "workbook": out_path.name,
            "state_change_count": len(changes),
            "state_changes": changes,
            "error": result["stderr"] or None if not result["ok"] else None,
        }
    )
    return result


def handle_slack_upload(payload: dict[str, object]) -> dict[str, object]:
    """관리 문서를 Slack 채널에 올린다.

    토큰과 채널 ID는 요청 본문으로 받지 않는다 — 마운트된
    ``RPAs/study-status-report/.env``를 ``slack_upload_file.py``가 직접 읽는다.
    n8n 워크플로나 실행 이력에 비밀값이 남지 않게 하려는 것이다.
    """
    file_path = _resolve_inside(REPORT_DIR, _require(payload, "file"))
    if not file_path.exists():
        raise RunnerError(f"업로드할 파일이 없습니다: {file_path}")

    args = [
        "--file", str(file_path),
        "--title", _require(payload, "title"),
        "--comment", _require(payload, "comment"),
    ]
    result = _skipped_by_dry_run("slack_upload") or _run("slack_upload_file.py", args)
    # 업로드에 실패해도 만들어 둔 파일은 지우지 않는다. 관리자가 그대로 올릴 수
    # 있어야 하기 때문이다(README 실패 조건: "파일은 보존하고 실패 로그를 남긴다").
    append_run_log(
        {
            "action": "slack_upload",
            "ok": result["ok"],
            "workbook": file_path.name,
            "error": result["stderr"] or None if not result["ok"] else None,
        }
    )
    return result


def handle_slack_message(payload: dict[str, object]) -> dict[str, object]:
    """첨부 없이 텍스트만 보낸다. 시간표를 읽지 못한 경우의 오류 알림에 쓴다.

    **같은 사유는 쿨다운 동안 한 번만 보낸다.** 시간표가 없는 상태는 사람이 파일을
    둘 때까지 이어지는데, 워크플로는 5분마다 도는 탓에 그대로 두면 하루 수백 건이
    채널에 쌓인다. 알림이 묻히면 정작 봐야 할 때 못 본다.

    쿨다운은 메모리에만 둔다. 컨테이너를 다시 띄우면 한 번 더 나가는데, 재기동은
    사람이 손댄 시점이라 상태를 다시 알리는 편이 낫다.
    """
    text = _require(payload, "text")
    reason = payload.get("reason")
    cooldown = payload.get("cooldown_seconds", DEFAULT_MESSAGE_COOLDOWN_SECONDS)
    cooldown = float(cooldown) if isinstance(cooldown, int | float) else DEFAULT_MESSAGE_COOLDOWN_SECONDS

    key = str(reason) if reason else text
    now = datetime.now(UTC)
    with _message_lock:
        last_sent = _last_message_at.get(key)
        if last_sent is not None and (now - last_sent).total_seconds() < cooldown:
            remaining = int(cooldown - (now - last_sent).total_seconds())
            logger.info("reason=%s 쿨다운 중이라 알림을 건너뜁니다(%s초 남음).", key, remaining)
            return {"ok": True, "skipped": True, "reason": "cooldown", "retry_after_seconds": remaining}
        # 전송 전에 기록한다. 전송이 느릴 때 다음 주기가 겹쳐 두 번 나가지 않게 한다.
        _last_message_at[key] = now

    result = _skipped_by_dry_run("slack_message") or _run("slack_upload_file.py", ["--message-only", "--comment", text])
    if not result["ok"]:
        # 실패했으면 쿨다운을 풀어 다음 주기에 다시 시도할 수 있게 한다.
        with _message_lock:
            _last_message_at.pop(key, None)
    append_run_log(
        {
            "action": "slack_message",
            "ok": result["ok"],
            "reason": reason,
            "error": result["stderr"] or None if not result["ok"] else None,
        }
    )
    return result


ROUTES = {
    "/workbook": handle_workbook,
    "/slack-upload": handle_slack_upload,
    "/slack-message": handle_slack_message,
}


class Handler(BaseHTTPRequestHandler):
    server_version = "rpa-runner"

    def _send(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler의 이름 규칙이다.
        if self.path == "/health":
            # dry_run을 함께 내보낸다. 켜 둔 것을 잊고 왜 Slack이 안 오는지
            # 찾는 일이 없도록 상태를 한눈에 보이게 한다.
            self._send(200, {"ok": True, "repo_dir": str(REPO_DIR), "dry_run": DRY_RUN})
            return
        self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler의 이름 규칙이다.
        handler = ROUTES.get(self.path)
        if handler is None:
            self._send(404, {"ok": False, "error": "not found"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send(400, {"ok": False, "error": "Content-Length가 올바르지 않습니다."})
            return
        if length > MAX_BODY_BYTES:
            self._send(413, {"ok": False, "error": "요청 본문이 너무 큽니다."})
            return

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._send(400, {"ok": False, "error": f"JSON을 읽지 못했습니다: {exc}"})
            return
        if not isinstance(payload, dict):
            self._send(400, {"ok": False, "error": "요청 본문은 JSON 객체여야 합니다."})
            return

        try:
            result = handler(payload)
        except RunnerError as exc:
            logger.warning("path=%s 잘못된 요청: %s", self.path, exc)
            self._send(400, {"ok": False, "error": str(exc)})
            return
        except subprocess.TimeoutExpired:
            logger.error("path=%s 스크립트가 %.0f초 안에 끝나지 않았습니다.", self.path, COMMAND_TIMEOUT_SECONDS)
            self._send(504, {"ok": False, "error": "스크립트 실행이 시간을 초과했습니다."})
            return
        except Exception:
            logger.exception("path=%s 처리 중 오류", self.path)
            self._send(500, {"ok": False, "error": "실행기 내부 오류"})
            return

        # 스크립트가 실패하면 그대로 실패로 돌려준다. n8n이 성공으로 넘기지 않게 한다.
        self._send(200 if result.get("ok") else 500, result)

    def log_message(self, format: str, *args: object) -> None:
        # 기본 구현은 stderr로 직접 찍는다. 로거를 거치게 해 형식을 맞춘다.
        logger.info("%s - %s", self.address_string(), format % args)


def main() -> None:
    logger.info("rpa-runner 시작 (port=%s, repo_dir=%s)", LISTEN_PORT, REPO_DIR)
    if not SCRIPT_DIR.exists():
        # 죽이지는 않는다. compose 기동 순서에 따라 마운트가 늦을 수 있고,
        # /health로 원인을 확인할 수 있어야 한다.
        logger.warning("스크립트 디렉터리가 없습니다: %s (저장소 마운트 확인 필요)", SCRIPT_DIR)
    ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
