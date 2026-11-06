"""The whole app in a real browser: every page, view, option, sample, action button and typed input
(scripts/clickthrough.py). It takes minutes and needs Chromium, so it runs with PLUMBLINE_E2E=1: in the
CI job of that name, or by hand. A second workflow runs the same walk against the live app."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.environ.get("PLUMBLINE_E2E") != "1",
                                reason="slow and needs a browser: set PLUMBLINE_E2E=1")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_every_page_option_button_and_typed_input_works_in_a_real_browser(tmp_path):
    pytest.importorskip("playwright")
    port = _free_port()
    env = {**os.environ, "PLUMBLINE_DEMO_DIR": str(tmp_path / "demo")}
    app = subprocess.Popen([sys.executable, "-m", "streamlit", "run", str(ROOT / "streamlit_app.py"),
                            "--server.port", str(port), "--server.headless", "true"],
                           env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(120):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=2)
                break
            except OSError:
                time.sleep(1)
        sys.path.insert(0, str(ROOT / "scripts"))
        from clickthrough import run
        problems, steps = run(f"http://127.0.0.1:{port}/")
        assert steps > 300, f"only {steps} checks ran: the walk is not reaching the app"
        assert not problems, f"{len(problems)} problems:\n" + "\n".join(problems[:40])
    finally:
        app.terminate()
        app.wait(timeout=30)
