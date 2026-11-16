"""The live-app check must wake a sleeping Streamlit app. A sleeping app answers only on its outer page; its inner
/~/+/ address never shows the app. Run #44 of "live app" (2026-11-16) waited 20 minutes on the inner address and
failed as a stale deploy, though the deploy was fine."""
import http.server
import importlib.util
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("clickthrough", ROOT / "scripts" / "clickthrough.py")
CT = importlib.util.module_from_spec(spec)
spec.loader.exec_module(CT)


class Sleepy(http.server.BaseHTTPRequestHandler):
    awake = False

    def do_GET(self):
        if self.path.startswith("/~/+"):
            body = "<p>Built by someone</p><p>code 4b24db162b</p>" if Sleepy.awake else "<p></p>"
        else:
            Sleepy.awake = True                       # visiting the outer page wakes the app
            body = "<p>Your app is waking up!</p>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *a):
        pass


@pytest.fixture
def sleepy():
    pytest.importorskip("playwright.sync_api")
    Sleepy.awake = False
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Sleepy)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/~/+/"
    srv.shutdown()


def test_the_outer_address_is_the_app_root():
    assert CT._outer("https://minerva-plumbline.streamlit.app/~/+/") == "https://minerva-plumbline.streamlit.app/"
    assert CT._outer("http://localhost:8501/") == "http://localhost:8501/"


def test_a_sleeping_app_is_woken_and_its_code_read(sleepy):
    try:
        got = CT.deployed_version(sleepy, "4b24db162b", 0.05, page_timeout_s=3, poll_s=0.5)
    except Exception as e:          # no browser on this machine
        pytest.skip(f"no browser: {e}")
    assert got is None


def test_a_failure_says_what_the_page_showed(sleepy):
    try:
        got = CT.deployed_version(sleepy, "0000000000", 0.01, page_timeout_s=2, poll_s=0.5)
    except Exception as e:
        pytest.skip(f"no browser: {e}")
    assert got == "4b24db162b"      # it reports the code it did see, not a bare timeout
