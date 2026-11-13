"""The third engineer's source (NYC 311 through the kit, 2026-11-13), kept in CI against a stand-in that answers
her queries the way Socrata does, including from a copy that is behind: empty pages and a null newest time,
marked X-SODA2-Data-Out-Of-Date. Many rows share one :updated_at, as after a bulk reload."""
import json
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from plumbline import kit

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "examples" / "sources" / "nyc_311.py"
CONTRACT = ROOT / "sources" / "kit" / "nyc_311.yaml"
WHERE = re.compile(r"created_date >= '([^']+)' AND \(:updated_at > '([^']+)'(?: OR \(:updated_at = '([^']+)' AND unique_key > '([^']+)'\))?\)")


def rows(n=7000, block=3000):
    out = []
    for i in range(n):
        upd = "2026-11-13T02:36:35.285Z" if i < block else f"2026-11-13T{3 + i // 600:02d}:{(i * 7) % 60:02d}:00.000Z"
        out.append({"unique_key": str(71000000 + i), "created_date": f"2026-11-12T{8 + i % 12:02d}:00:00.000",
                    "agency": "NYPD" if i % 2 else "DSNY", "complaint_type": "Noise - Residential", "descriptor": "Loud Music/Party",
                    "status": "Open", "location_type": "Residential Building", "incident_zip": "11211", "borough": "BROOKLYN",
                    "community_board": "01 BROOKLYN", "council_district": "33", "police_precinct": "Precinct 90",
                    "open_data_channel_type": "PHONE", ":updated_at": upd})
    return out


@pytest.fixture
def api():
    state = {"rows": rows(), "stale": 0}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
            stale = state["stale"] > 0
            if stale:
                state["stale"] -= 1
            if q["$select"].startswith("max(:updated_at)"):
                since = q["$where"].split("'")[1]
                held = [r[":updated_at"] for r in state["rows"] if r["created_date"] >= since]
                body = [{}] if stale or not held else [{"newest": max(held)}]
            else:
                m = WHERE.fullmatch(q["$where"])
                assert m, q["$where"]
                since, at, _, after = m.groups()
                assert q["$order"] == ":updated_at, unique_key"
                sel = [r for r in state["rows"] if r["created_date"] >= since and
                       (r[":updated_at"] > at or (after and r[":updated_at"] == at and r["unique_key"] > after))]
                sel.sort(key=lambda r: (r[":updated_at"], r["unique_key"]))
                body = [] if stale else sel[:int(q["$limit"])]
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            if stale:
                self.send_header("X-SODA2-Data-Out-Of-Date", "true")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cls = kit._load(str(SOURCE))
    cls.url, cls.base_delay = f"http://127.0.0.1:{srv.server_address[1]}/resource/erm2-nwe9.json", 0.0
    yield cls, state
    srv.shutdown()


def test_keyset_paging_reads_every_request_once_through_a_block_that_shares_one_update_time(tmp_path, api):
    cls, _ = api
    r = kit.run(cls(), workdir=tmp_path / "w", contract=CONTRACT)
    assert (r.rows_landed, r.rows_loaded, r.rows_quarantined) == (7000, 7000, 0) and r.pages == 2
    again = kit.run(cls(), workdir=tmp_path / "w", contract=CONTRACT)
    assert again.rows_landed == 0 and again.cursor == r.cursor


def test_an_empty_page_from_the_copy_that_is_behind_is_not_taken_as_the_end(tmp_path, api):
    cls, state = api
    state["stale"] = 3                                    # the first page and two newest-time checks come back stale
    r = kit.run(cls(), workdir=tmp_path / "w", contract=CONTRACT)
    assert r.rows_loaded == 7000


def test_it_passes_the_kits_verify(tmp_path, api):
    cls, _ = api
    checks = kit.verify(cls, contract=CONTRACT, workdir=tmp_path / "v")
    assert [ok for _, ok, _ in checks] == [True, True, True, True], checks
