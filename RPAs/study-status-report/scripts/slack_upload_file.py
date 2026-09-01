"""Upload a report file to Slack using current external upload APIs."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


SLACK_API = "https://slack.com/api"
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def api_call(method: str, token: str, fields: dict[str, str]) -> dict[str, Any]:
    body = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(
        f"{SLACK_API}/{method}",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError(f"Slack API {method} failed: {payload.get('error', 'unknown_error')}")
    return payload


def post_file_bytes(upload_url: str, path: Path) -> None:
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    request = urllib.request.Request(
        upload_url,
        data=path.read_bytes(),
        headers={"Content-Type": mime_type},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"Slack file byte upload failed with status {response.status}")


def upload_file(
    token: str, channel_id: str, paths: list[Path], title: str, comment: str
) -> dict[str, Any]:
    """파일 여러 개를 **한 메시지로** 올린다.

    관리 문서(.xlsx)와 같은 내용의 HTML을 함께 보내는데, 따로 올리면 채널에 메시지가
    두 번 쌓이고 둘이 짝이라는 것도 드러나지 않는다. Slack은 업로드 URL을 파일마다
    받고 완료 호출 한 번에 묶어 보내는 방식이라 그대로 쓴다.

    제목은 첫 파일에만 그대로 붙이고, 나머지는 파일 이름을 쓴다. 같은 제목이 여러 개
    붙으면 어느 것이 무엇인지 구분되지 않는다.
    """
    if not paths:
        raise ValueError("업로드할 파일이 없습니다.")
    entries = []
    for index, path in enumerate(paths):
        if not path.exists():
            raise FileNotFoundError(path)
        upload = api_call(
            "files.getUploadURLExternal",
            token,
            {"filename": path.name, "length": str(path.stat().st_size)},
        )
        post_file_bytes(upload["upload_url"], path)
        entries.append({"id": upload["file_id"], "title": title if index == 0 else path.name})

    return api_call(
        "files.completeUploadExternal",
        token,
        {
            "files": json.dumps(entries, ensure_ascii=False),
            "channel_id": channel_id,
            "initial_comment": comment,
        },
    )


def post_webhook(webhook_url: str, text: str) -> None:
    request = urllib.request.Request(
        webhook_url,
        data=json.dumps({"text": text}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read().decode("utf-8", errors="replace")
    if body.strip().lower() != "ok":
        raise RuntimeError("Slack webhook did not return ok")


def post_message(token: str, channel_id: str, text: str) -> dict[str, Any]:
    """파일 없이 텍스트만 보낸다.

    시간표를 읽지 못했을 때처럼 첨부할 산출물이 없는 오류 상황에 쓴다. Incoming
    Webhook을 쓰지 않는 이유는 별도 발급이 필요해서다 — 파일 업로드에 이미 쓰는
    Bot token에 chat:write가 있으면 그대로 보낼 수 있다.
    """
    return api_call("chat.postMessage", token, {"channel": channel_id, "text": text})


def parse_args() -> argparse.Namespace:
    load_env_file(ENV_FILE)
    parser = argparse.ArgumentParser()
    # 메시지만 보내는 모드에서는 첨부할 파일이 없다.
    # 여러 번 줄 수 있다. 관리 문서와 같은 내용의 HTML을 함께 보낼 때 쓴다.
    parser.add_argument("--file", type=Path, action="append", dest="files")
    parser.add_argument("--title")
    parser.add_argument("--comment", required=True)
    parser.add_argument("--token", default=os.environ.get("SLACK_BOT_TOKEN", ""))
    parser.add_argument("--channel-id", default=os.environ.get("SLACK_CHANNEL_ID", ""))
    parser.add_argument("--webhook-url", default=os.environ.get("SLACK_WEBHOOK_URL", ""))
    parser.add_argument("--webhook-only", action="store_true")
    parser.add_argument("--message-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.webhook_only:
        if not args.webhook_url:
            raise SystemExit("SLACK_WEBHOOK_URL is required for webhook-only mode")
        post_webhook(args.webhook_url, args.comment)
        print("OK: Slack webhook message sent")
        return

    if args.message_only:
        if not args.token:
            raise SystemExit("SLACK_BOT_TOKEN is required")
        if not args.channel_id:
            raise SystemExit("SLACK_CHANNEL_ID is required for message")
        post_message(args.token, args.channel_id, args.comment)
        print("OK: Slack message sent")
        return

    if not args.token:
        raise SystemExit("SLACK_BOT_TOKEN is required")
    if not args.channel_id:
        raise SystemExit("SLACK_CHANNEL_ID is required for file upload")
    if not args.files or args.title is None:
        raise SystemExit("--file and --title are required for file upload")

    result = upload_file(args.token, args.channel_id, args.files, args.title, args.comment)
    file_ids = [item.get("id") for item in result.get("files", [])]
    print(f"OK: Slack file upload complete ({', '.join(file_ids)})")


if __name__ == "__main__":
    try:
        main()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Slack HTTP error {exc.code}: {detail}") from exc
