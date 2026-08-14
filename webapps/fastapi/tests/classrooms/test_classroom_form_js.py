"""강의실 생성·수정 폼(`form[data-api-url]`) 제출 계약 회귀 테스트.

TASK-004 code review BLOCKER: classroom-admin.js를 통합 좌석 관리 컨트롤러로
재작성하면서 create.html/edit.html이 여전히 의존하는 범용
`form[data-api-url]` 제출 핸들러(및 `collectPayload` 헬퍼)가 통째로
삭제됐었다. 기존 `test_admin_pages.py`는 HTML에 `data-api-url` 속성이
있는지만 확인하고 JS가 실제로 그 속성을 읽어 fetch를 호출하는지는
검증하지 못해 회귀를 놓쳤다.

정적 검증(브라우저 불필요): 소스에 핸들러·헬퍼가 존재하고 seat 컨트롤러와
분리돼 있는지 문자열/정규식으로 확인한다.

브라우저 검증(Edge/Chrome headless, 없으면 skip): 실제 DOM에서 폼을
제출했을 때 fetch가 올바른 method/url/payload로 호출되고, API 오류
message가 화면에 표시되는지 확인한다 (성공 시 `window.location.href`
이동은 실제 네비게이션을 유발하므로 실패 응답 경로만 검증한다;
`test_seat_admin_js.py`와 동일한 이유).
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
HARNESS_TEMPLATE = Path(__file__).parent / "browser_harness" / "classroom_form_test.html"

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

    with tempfile.TemporaryDirectory() as profile_dir:
        html_file = Path(profile_dir) / "classroom_form_test.html"
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


def _checkpoint(results: dict[str, object], name: str) -> dict[str, object]:
    for checkpoint in _checkpoints(results):
        if checkpoint["name"] == name:
            return checkpoint
    raise AssertionError(f"checkpoint가 없습니다: {name}")


def _assert_all_checkpoints(results: dict[str, object]) -> None:
    assert results.get("done"), f"시나리오가 완료되지 않았습니다: {results.get('error')}"
    failed = [c for c in _checkpoints(results) if not c["ok"]]
    assert not failed, f"실패한 검증: {failed}"


# ── 정적 검증 (브라우저 불필요) ───────────────────────────────────────────────


class TestClassroomFormJsStatic:
    def test_bundle_has_generic_data_api_url_handler(self) -> None:
        """`form[data-api-url]` 제출 핸들러가 소스에 존재한다 (create/edit.html이 의존)."""
        source = ADMIN_JS.read_text(encoding="utf-8")
        assert 'document.querySelectorAll("form[data-api-url]")' in source
        assert "form.dataset.apiUrl" in source
        assert "form.dataset.apiMethod" in source

    def test_bundle_has_collect_payload_helper(self) -> None:
        """`collectPayload` 헬퍼가 정의되어 있고 제출 핸들러가 사용한다."""
        source = ADMIN_JS.read_text(encoding="utf-8")
        assert "function collectPayload(form)" in source
        assert "collectPayload(form)" in source.split("function collectPayload", 1)[0]

    def test_generic_handler_redirects_to_success_url_on_ok(self) -> None:
        """성공(ok) 시 `data-success-url`(없으면 /classrooms)로 이동한다."""
        source = ADMIN_JS.read_text(encoding="utf-8")
        assert 'window.location.href = form.dataset.successUrl || "/classrooms"' in source

    def test_generic_handler_shows_api_error_message(self) -> None:
        """실패 시 error envelope의 message를 `data-form-error`에 표시한다."""
        source = ADMIN_JS.read_text(encoding="utf-8")
        assert "body.error.message" in source
        assert "showError(message)" in source

    def test_generic_handler_precedes_seat_admin_controller(self) -> None:
        """제네릭 폼 핸들러는 seat 컨트롤러 IIFE보다 앞서 등록되고,
        seat 컨트롤러는 `#seat-grid`가 없는 페이지(create/edit.html)에서
        조기 반환해 서로 간섭하지 않는다."""
        source = ADMIN_JS.read_text(encoding="utf-8")
        generic_index = source.index("form[data-api-url]")
        seat_index = source.index("seatAdminController")
        assert generic_index < seat_index
        assert "if (!grid || !panel || !form) return;" in source


# ── 브라우저 검증 (headless, 없으면 skip) ────────────────────────────────────


class TestClassroomFormJsBrowser:
    def test_create_form_submits_expected_payload(self, browser_results: dict[str, object]) -> None:
        """강의실 생성 폼 제출은 POST /api/v1/classrooms를 code/name/location payload로 호출한다."""
        _assert_all_checkpoints(browser_results)
        checkpoint = _checkpoint(browser_results, "create-form-posts-with-payload")
        assert checkpoint["ok"], f"생성 폼 payload 위반: {checkpoint['detail']}"

    def test_create_form_shows_api_error_message(self, browser_results: dict[str, object]) -> None:
        """API 오류 응답의 message가 생성 폼 오류 영역에 표시된다."""
        _assert_all_checkpoints(browser_results)
        checkpoint = _checkpoint(browser_results, "create-form-shows-api-error")
        assert checkpoint["ok"], f"오류 메시지 미표시: {checkpoint['detail']}"

    def test_edit_form_submits_expected_payload_including_checkbox(
        self, browser_results: dict[str, object]
    ) -> None:
        """강의실 수정 폼 제출은 PUT으로 code/name/location과 is_active(checkbox)를 함께 보낸다."""
        _assert_all_checkpoints(browser_results)
        checkpoint = _checkpoint(browser_results, "edit-form-puts-with-payload")
        assert checkpoint["ok"], f"수정 폼 payload 위반: {checkpoint['detail']}"
