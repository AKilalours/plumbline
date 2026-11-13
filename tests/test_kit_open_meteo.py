"""The second engineer's source (Open-Meteo hourly weather, added through the kit on 2026-11-13), kept in CI
against a local stand-in for the archive API: same request and response shape, made-up weather."""
import json
import threading
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from plumbline import kit

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples" / "sources" / "open_meteo_weather.py"
CONTRACT = ROOT / "sources" / "kit" / "open_meteo_weather.yaml"


@pytest.fixture
def archive():
    seen = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
            seen.append(q)
            d = date.fromisoformat(q["start_date"])
            hours = [f"{d.isoformat()}T{h:02d}:00" for h in range(24)]
            known = d <= datetime.now(timezone.utc).date() - timedelta(days=5)     # the archive trails today
            vals = lambda f: [(round(5 + h * 0.3, 1) if f != "weather_code" else 3) if known else None for h in range(24)]
            body = {"hourly": {"time": hours, **{f: vals(f) for f in q["hourly"].split(",")}}}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cls = kit._load(str(SOURCE))
    cls.url = f"http://127.0.0.1:{srv.server_address[1]}/v1/archive"
    yield cls, seen
    srv.shutdown()


def test_it_lands_a_day_per_page_with_24_hours_and_loads_under_its_reviewed_contract(tmp_path, archive):
    cls, seen = archive
    r = kit.run(cls(), workdir=tmp_path / "w", contract=CONTRACT, max_pages=3)
    assert (r.pages, r.rows_landed, r.rows_loaded, r.rows_quarantined) == (3, 72, 72, 0)
    assert r.cursor == "2026-11-03" and seen[0]["start_date"] == "2026-11-01" and seen[0]["timezone"] == "UTC"


def test_the_next_run_starts_the_day_after_the_cursor(tmp_path, archive):
    cls, seen = archive
    kit.run(cls(), workdir=tmp_path / "w", contract=CONTRACT, max_pages=3)
    seen.clear()
    kit.run(cls(), workdir=tmp_path / "w", contract=CONTRACT, max_pages=1)
    assert seen[0]["start_date"] == "2026-11-04"


def test_it_passes_the_kits_verify(tmp_path, archive):
    cls, _ = archive
    checks = kit.verify(cls, contract=CONTRACT, workdir=tmp_path / "v")
    assert all(ok is not False for _, ok, _ in checks), checks
