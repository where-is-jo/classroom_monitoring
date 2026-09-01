"""보고서를 메일로 보낸다. 첨부는 선택이다.

**표준 라이브러리만 쓴다.** 실행기 컨테이너에는 pip이 없다(`runner/Dockerfile`).
`smtplib`과 `email.message`가 표준 라이브러리라 의존성이 늘지 않는다.

**자격 증명은 요청 본문으로 받지 않는다.** Slack 토큰과 같은 방식으로 마운트된
`RPAs/study-status-report/.env`를 이 스크립트가 직접 읽는다 — n8n 워크플로나 실행
이력에 비밀값이 남지 않게 하려는 것이다.

**메일은 Slack 비공개 채널과 다르다.** 전달되고, 개인 사서함에 남고, 보존 기간을
통제할 수 없다. 첨부되는 관리 문서에는 학생 실명과 좌석이 들어 있으므로 수신자
목록(`REPORT_EMAIL_TO`)을 늘릴 때는 그 점을 함께 판단한다.

    python RPAs/study-status-report/scripts/send_email.py \\
      --subject "주간 자습 현황" --body "본문" --file reports/weekly_....xlsx
"""

from __future__ import annotations

import argparse
import mimetypes
import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
# 첨부는 관리 문서 한 개다. 메일 서버가 대개 25MB 근처에서 막으므로 그 앞에서 끊는다.
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def split_recipients(value: str) -> list[str]:
    """쉼표나 세미콜론으로 나눈 주소 목록. 빈 항목은 버린다."""
    parts = value.replace(";", ",").split(",")
    return [item.strip() for item in parts if item.strip()]


def build_message(
    sender: str, recipients: list[str], subject: str, body: str, attachments: list[Path]
) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject
    message.set_content(body)

    total = 0
    for attachment in attachments:
        payload = attachment.read_bytes()
        total += len(payload)
        if total > MAX_ATTACHMENT_BYTES:
            raise SystemExit(f"첨부 합계가 너무 큽니다: {total} bytes (상한 {MAX_ATTACHMENT_BYTES})")
        guessed = mimetypes.guess_type(attachment.name)[0] or "application/octet-stream"
        maintype, _, subtype = guessed.partition("/")
        message.add_attachment(
            payload, maintype=maintype, subtype=subtype or "octet-stream", filename=attachment.name
        )
    return message


def send(message: EmailMessage, host: str, port: int, username: str, password: str, use_tls: bool) -> None:
    """SMTP로 보낸다.

    465는 처음부터 TLS(SMTPS), 그 밖에는 평문으로 열고 STARTTLS로 올린다. Gmail·
    Workspace는 587 + STARTTLS이고 앱 비밀번호가 필요하다.
    """
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=60) as server:
            if username:
                server.login(username, password)
            server.send_message(message)
        return

    with smtplib.SMTP(host, port, timeout=60) as server:
        server.ehlo()
        if use_tls:
            server.starttls(context=context)
            server.ehlo()
        if username:
            server.login(username, password)
        server.send_message(message)


def parse_args() -> argparse.Namespace:
    load_env_file(ENV_FILE)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--body", required=True)
    # 여러 번 줄 수 있다. 관리 문서와 같은 내용의 HTML을 함께 붙일 때 쓴다.
    parser.add_argument(
        "--file", type=Path, action="append", dest="files", help="첨부할 보고서. 없으면 본문만 보낸다"
    )
    parser.add_argument("--to", default=os.environ.get("REPORT_EMAIL_TO", ""))
    parser.add_argument("--host", default=os.environ.get("SMTP_HOST", ""))
    parser.add_argument("--port", type=int, default=int(os.environ.get("SMTP_PORT", "587")))
    parser.add_argument("--username", default=os.environ.get("SMTP_USERNAME", ""))
    parser.add_argument("--password", default=os.environ.get("SMTP_PASSWORD", ""))
    parser.add_argument("--sender", default=os.environ.get("SMTP_FROM", ""))
    parser.add_argument(
        "--no-starttls",
        action="store_true",
        default=os.environ.get("SMTP_STARTTLS", "true").strip().lower() in {"0", "false", "no"},
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.host:
        raise SystemExit("SMTP_HOST가 필요합니다(.env).")
    recipients = split_recipients(args.to)
    if not recipients:
        raise SystemExit("REPORT_EMAIL_TO가 필요합니다(.env). 쉼표로 여러 명을 적을 수 있습니다.")
    sender = args.sender or args.username
    if not sender:
        raise SystemExit("SMTP_FROM 또는 SMTP_USERNAME이 필요합니다(.env).")
    attachments = args.files or []
    for attachment in attachments:
        if not attachment.is_file():
            raise SystemExit(f"첨부할 파일이 없습니다: {attachment}")

    message = build_message(sender, recipients, args.subject, args.body, attachments)
    try:
        send(message, args.host, args.port, args.username, args.password, not args.no_starttls)
    except smtplib.SMTPAuthenticationError as error:
        # 비밀번호 자체는 찍지 않는다. 계정만 알려도 원인 파악에 충분하다.
        raise SystemExit(f"SMTP 인증에 실패했습니다({args.username}): {error.smtp_code}") from None
    except (smtplib.SMTPException, OSError) as error:
        raise SystemExit(f"메일 전송에 실패했습니다: {error}") from None

    attached = ", ".join(item.name for item in attachments) or "없음"
    print(f"OK: 메일 전송 완료 (수신 {len(recipients)}명, 첨부 {attached})")


if __name__ == "__main__":
    main()
