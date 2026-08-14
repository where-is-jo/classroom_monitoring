"""TASK-004: classroom-admin.js 정적 검증 + 브라우저 계약 테스트.

정적 검증(브라우저 불필요):
  - 빈 칸 클릭이 `/seats/auto` POST를 호출하고 code/label prompt가 없다.
  - tray/unplace/is_active 관련 코드가 제거되었다.
  - danger 확인 대화상자(영구 삭제·해제)가 존재하고 성공 시 reload로 동기화한다.

브라우저 검증(Edge/Chrome headless, 없으면 skip):
  - 빈 칸 클릭 → `POST /seats/auto` (row/column만, code prompt 없음)
  - 생성 실패(422·500) 시 브라우저 재시도 없음 (fetch 정확히 1회)
  - 좌석 클릭 → 편집 패널 (label·학생만, code/is_active 입력 없음)
  - 삭제·해제는 danger 확인 대화상자(영구 삭제·해제 명시) 후 DELETE, 취소 시 미호출

주의: 성공 응답은 `location.reload()`를 유발하므로(화면 동기화) 대역 harness에서
reload를 막을 수 없다(Location.reload는 unforgeable). 따라서 mutation 검증은
실패 응답으로 요청 계약만 확인하고, 성공 시 reload 여부는 정적 검증이 담당한다.
HTML contract(정적 검증)는 `test_ui_contract.py`가 담당한다.
"""

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
ADMIN_JS = BASE / "static" / "classroom-admin.js"
HARNESS_TEMPLATE = Path(__file__).parent / "browser_harness" / "seat_admin_test.html"

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
    """headless 브라우저에서 classroom-admin.js를 실행한 결과 JSON."""
    if BROWSER is None:
        pytest.skip("Edge/Chrome headless가 없어 브라우저 테스트를 건너뜁니다.")
    harness = HARNESS_TEMPLATE.read_text(encoding="utf-8")
    harness = harness.replace("{{ ADMIN_JS_URI }}", ADMIN_JS.resolve().as_uri())

    # 브라우저 첫 실행 프로필 충돌을 피하기 위해 사용자 데이터를 임시 디렉터리에 둔다.
    with tempfile.TemporaryDirectory() as profile_dir:
        html_file = Path(profile_dir) / "seat_admin_test.html"
        html_file.write_text(harness, encoding="utf-8")

        command = [
            BROWSER,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--allow-file-access-from-files",
            "--virtual-time-budget=5000",
            f"--user-data-dir={profile_dir}",
            "--dump-dom",
            html_file.as_uri(),
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        dom = completed.stdout

    if not dom:
        raise AssertionError(f"브라우저 출력이 비어 있습니다. stderr: {completed.stderr[:2000]}")
    return _extract_results(dom)


def _extract_results(dom: str) -> dict[str, object]:
    match = re.search(r'<pre id="test-results">(.*?)</pre>', dom, re.DOTALL)
    if not match:
        raise AssertionError("dump-dom에서 test-results 요소를 찾지 못했습니다.")
    text = html_module.unescape(match.group(1))
    return cast(dict[str, object], json.loads(text))


def _checkpoints(results: dict[str, object]) -> list[dict[str, object]]:
    value = results.get("checkpoints", [])
    assert isinstance(value, list)
    return [item for item in value if isinstance(item, dict)]


def _assert_all_checkpoints(results: dict[str, object]) -> None:
    assert results.get("done"), f"시나리오가 완료되지 않았습니다: {results.get('error')}"
    failed = [c for c in _checkpoints(results) if not c["ok"]]
    assert not failed, f"실패한 검증: {failed}"


def _checkpoint(results: dict[str, object], name: str) -> dict[str, object]:
    for checkpoint in _checkpoints(results):
        if checkpoint["name"] == name:
            return checkpoint
    raise AssertionError(f"checkpoint가 없습니다: {name}")


# ── 정적 검증 (브라우저 불필요) ───────────────────────────────────────────────


class TestSeatAdminJsStatic:
    def test_bundle_posts_seats_auto_with_row_column(self) -> None:
        """빈 칸 클릭은 POST /seats/auto를 row/column만으로 호출한다."""
        source = ADMIN_JS.read_text(encoding="utf-8")
        assert "/seats/auto" in source
        assert 'method: "POST"' in source
        assert "JSON.stringify({ row, column })" in source

    def test_bundle_has_no_code_prompt(self) -> None:
        """code/label 입력 prompt가 없다 (code는 서버 allocator가 할당)."""
        source = ADMIN_JS.read_text(encoding="utf-8")
        assert "window.prompt" not in source
        assert "prompt(" not in source

    def test_bundle_has_no_tray_or_unplace_code(self) -> None:
        """tray·unplace 관련 코드가 제거되었다."""
        source = ADMIN_JS.read_text(encoding="utf-8")
        assert "seat-tray" not in source
        assert "seat-unplace" not in source
        assert "unplace" not in source
        assert "tray" not in source

    def test_bundle_does_not_send_is_active(self) -> None:
        """is_active를 payload로 보내지 않는다."""
        source = ADMIN_JS.read_text(encoding="utf-8")
        assert "is_active" not in source

    def test_bundle_has_permanent_confirm_dialogs(self) -> None:
        """삭제·해제는 '영구 삭제·해제'를 명시한 확인 대화상자를 쓴다."""
        source = ADMIN_JS.read_text(encoding="utf-8")
        assert "window.confirm" in source
        assert "영구 삭제" in source
        assert "영구 해제" in source

    def test_bundle_reloads_on_success_without_retry(self) -> None:
        """성공·409 충돌 시 reload로 화면을 동기화한다 (자동 재시도 없음).

        브라우저가 실패한 요청을 다시 보내지 않으므로, 성공 경로의 reload는
        재시도가 아니라 서버 상태 반영이다.
        """
        source = ADMIN_JS.read_text(encoding="utf-8")
        assert "window.location.reload" in source
        # 성공(ok) 분기에서만 reload한다 (실패 분기에는 reload가 없다).
        assert "if (response.ok)" in source
        assert "response.status === 409" in source
        # 자동 재시도 루프(재귀 fetch 호출)가 없다.
        assert "retry" not in source.lower()


# ── 브라우저 검증 (headless, 없으면 skip) ────────────────────────────────────


class TestSeatAdminJsBrowser:
    def test_scenarios_all_pass(self, browser_results: dict[str, object]) -> None:
        """빈 칸 auto 생성·재시도 없음·danger 확인 대화상자 시나리오 전체."""
        _assert_all_checkpoints(browser_results)

    def test_empty_cell_click_posts_seats_auto(self, browser_results: dict[str, object]) -> None:
        """빈 칸 클릭 → POST /seats/auto (row/column만, code 입력 없음)."""
        _assert_all_checkpoints(browser_results)
        checkpoint = _checkpoint(browser_results, "empty-cell-create-calls-auto")
        assert checkpoint["ok"], f"auto POST 계약 위반: {checkpoint['detail']}"

    def test_empty_cell_create_uses_no_prompt(self, browser_results: dict[str, object]) -> None:
        """code/label prompt를 사용하지 않는다."""
        _assert_all_checkpoints(browser_results)
        checkpoint = _checkpoint(browser_results, "empty-cell-create-no-prompt")
        assert checkpoint["ok"], f"prompt 사용: {checkpoint['detail']}"

    def test_create_failure_does_not_retry(self, browser_results: dict[str, object]) -> None:
        """422·500 실패에도 브라우저가 auto POST를 재시도하지 않는다."""
        _assert_all_checkpoints(browser_results)
        for name in ("create-failure-no-retry", "create-500-no-retry"):
            checkpoint = _checkpoint(browser_results, name)
            assert checkpoint["ok"], f"재시도 발생: {checkpoint['detail']}"

    def test_create_failure_shows_alert(self, browser_results: dict[str, object]) -> None:
        """실패 메시지는 aria-live alert region으로 표시된다."""
        _assert_all_checkpoints(browser_results)
        checkpoint = _checkpoint(browser_results, "create-failure-shows-alert")
        assert checkpoint["ok"], f"alert 미표시: {checkpoint['detail']}"

    def test_occupied_cell_opens_panel_without_code_active(
        self,
        browser_results: dict[str, object],
    ) -> None:
        """좌석 클릭 → 편집 패널에 label·학생만 있고 code/is_active 입력이 없다."""
        _assert_all_checkpoints(browser_results)
        for name in (
            "occupied-cell-opens-panel",
            "panel-shows-label",
            "panel-has-no-code-input",
            "panel-has-no-active-input",
            "release-enabled-when-assigned",
        ):
            checkpoint = _checkpoint(browser_results, name)
            assert checkpoint["ok"], f"{name}: {checkpoint['detail']}"

    def test_delete_confirms_permanent_then_deletes(
        self,
        browser_results: dict[str, object],
    ) -> None:
        """삭제는 '영구 삭제' 확인 대화상자 후 DELETE를 호출하고 재시도하지 않는다."""
        _assert_all_checkpoints(browser_results)
        for name in (
            "delete-confirm-message-permanent",
            "delete-issues-delete-after-confirm",
            "delete-failure-no-retry",
        ):
            checkpoint = _checkpoint(browser_results, name)
            assert checkpoint["ok"], f"{name}: {checkpoint['detail']}"

    def test_delete_cancel_issues_no_request(self, browser_results: dict[str, object]) -> None:
        """확인 대화상자에서 취소하면 DELETE를 호출하지 않는다."""
        _assert_all_checkpoints(browser_results)
        checkpoint = _checkpoint(browser_results, "delete-cancel-no-request")
        assert checkpoint["ok"], f"취소 후 DELETE 호출: {checkpoint['detail']}"

    def test_release_confirms_then_unassigns(self, browser_results: dict[str, object]) -> None:
        """해제는 '영구 해제' 확인 대화상자 후 assignment DELETE를 호출한다."""
        _assert_all_checkpoints(browser_results)
        for name in ("release-confirm-message", "release-issues-delete-after-confirm"):
            checkpoint = _checkpoint(browser_results, name)
            assert checkpoint["ok"], f"{name}: {checkpoint['detail']}"
