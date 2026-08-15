"""강의실 좌석 현황 SSE 브라우저 계약 테스트."""

from __future__ import annotations

import html as html_module
import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import cast

import pytest

BASE = Path(__file__).resolve().parents[2]
CLASSROOMS_JS = BASE / "static" / "classrooms.js"
HARNESS_TEMPLATE = Path(__file__).parent / "browser_harness" / "classrooms_test.html"

_BROWSER_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)


def _find_browser() -> str | None:
    for candidate in _BROWSER_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


BROWSER = _find_browser()


@pytest.fixture(scope="module")
def browser_results() -> dict[str, object]:
    """headless 브라우저에서 classrooms.js SSE 시나리오를 실행한다."""
    if BROWSER is None:
        pytest.skip("Edge/Chrome headless가 없어 브라우저 테스트를 건너뜁니다.")
    harness = HARNESS_TEMPLATE.read_text(encoding="utf-8").replace(
        "{{ CLASSROOMS_JS_URI }}", CLASSROOMS_JS.resolve().as_uri()
    )

    with tempfile.TemporaryDirectory() as profile_dir:
        html_file = Path(profile_dir) / "classrooms_test.html"
        html_file.write_text(harness, encoding="utf-8")
        completed = subprocess.run(
            [
                BROWSER,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--allow-file-access-from-files",
                "--virtual-time-budget=3000",
                f"--user-data-dir={profile_dir}",
                "--dump-dom",
                html_file.as_uri(),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )

    match = re.search(r'<pre id="test-results">(.*?)</pre>', completed.stdout, re.DOTALL)
    if match is None:
        raise AssertionError(f"브라우저 결과가 없습니다. stderr: {completed.stderr[:2000]}")
    return cast(dict[str, object], json.loads(html_module.unescape(match.group(1))))


def test_realtime_list_card_scenario(browser_results: dict[str, object]) -> None:
    """일반 좌석 카드·집계·관측 시각이 SSE 이벤트로 함께 갱신된다."""
    assert browser_results.get("done"), browser_results.get("error")
    checkpoints = browser_results.get("checkpoints")
    assert isinstance(checkpoints, list)
    failed = [item for item in checkpoints if isinstance(item, dict) and not item.get("ok")]
    assert not failed, failed


def test_bundle_preserves_card_classes() -> None:
    """상태 갱신은 list 카드 레이아웃 클래스를 덮어쓰지 않는다."""
    source = CLASSROOMS_JS.read_text(encoding="utf-8")
    assert "seatEl.className =" not in source
    assert "seatEl.classList.remove" in source
