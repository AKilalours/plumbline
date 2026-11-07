"""The click-through's typing step on a busy app. On 2026-11-07 the live check typed into a chat box Streamlit had
disabled while it answered the previous message; Playwright gave up after 30 seconds and the traceback ended
the whole walk, so every later check went unreported. A box that is slow must be waited for, and one that never
comes back must be a recorded problem, not a crash."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _walker(page):
    spec = importlib.util.spec_from_file_location("clickthrough", ROOT / "scripts" / "clickthrough.py")
    ct = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ct)
    w = ct.Walker(page, "http://localhost/")
    w.idle = lambda timeout=0: None               # no Streamlit here: nothing to wait for after typing
    return w


@pytest.fixture
def page():
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:
            pytest.skip(f"no browser: {e}")
        yield b.new_page()
        b.close()


def test_a_box_disabled_for_a_moment_is_waited_for_and_typed_into(page):
    page.set_content("""<textarea id="t" data-testid="stChatInputTextArea" disabled></textarea>
        <script>setTimeout(() => document.getElementById('t').disabled = false, 1500)</script>""")
    w = _walker(page)
    w.type_into(page.locator("#t"), "did any loads fail?", "agent › typed", wait=10)
    assert w.problems == [] and page.locator("#t").input_value().strip() == "did any loads fail?"


def test_a_box_that_never_comes_back_is_a_recorded_problem_not_a_crash(page):
    page.set_content('<textarea id="t" data-testid="stChatInputTextArea" disabled></textarea>')
    w = _walker(page)
    w.type_into(page.locator("#t"), "   ", "agent › typed '   '", wait=1)
    (p,) = w.problems
    assert p.startswith("agent › typed '   ': could not type")
