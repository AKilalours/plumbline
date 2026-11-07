"""One scheduled window of a live public event stream (Wikimedia's recent changes), recorded either way.

    python -m plumbline.ingest.stream_daily                 # what the daily GitHub job runs
    python -m plumbline.ingest.stream_daily --seconds 30    # a shorter window

Each run: read the stream for a fixed window (two minutes by default), landing micro-batches; half-way
through, hang up on purpose and resume with Last-Event-ID, counting how many events after the reconnect were
ones already seen (a real check of resume on a stream nobody here controls); load every batch under the
reviewed contract (sources/streams/wikimedia_recentchange.yaml) into a scratch warehouse; publish to the cloud
warehouse when MOTHERDUCK_TOKEN is set; and append one line to docs/results/streams/runs.jsonl: events,
rows loaded and quarantined, duplicates dropped, reconnects by cause, the resume check, events a second, and
lag (seconds from the stream's own event time to the event landing here; Wikimedia stamps that
time a second or two after the edit itself) at the median and 95th percentile.

Each run starts from the live edge, not from yesterday's checkpoint: catching up a day of this stream would
be millions of events for a sample job. Resume is exercised inside every run instead, and by the tests.
The landed rows are not kept here; who made each edit and comments never reach them.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "docs" / "results" / "streams"
NAME = "wikimedia_recentchange"
TITLE = "Wikimedia recent changes (live)"
HISTORY_URL = "https://raw.githubusercontent.com/AKilalours/plumbline/open-data-history/stream_runs.jsonl"


def run(*, url: str | None = None, seconds: float = 120.0, max_events: int = 50_000, out: Path = OUT,
        contracts: Path = ROOT / "sources" / "streams", publish_to: str | None = None, client_kw: dict | None = None,
        now=lambda: datetime.now(timezone.utc)) -> dict:
    import duckdb
    from .connectors.sse import DROPPED, WIKIMEDIA_URL, SSEClient, recentchange_row, stream_sync, _pct
    from .contracts import load_contracts
    from .engine import ingest

    started = now()
    rec = {"run_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"), "name": NAME, "title": TITLE, "url": url or WIKIMEDIA_URL,
           "window_seconds": seconds, "ok": False, "dropped_fields": DROPPED}
    t0 = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            landing = Path(tmp) / "run" / "sources" / NAME
            client = SSEClient(url or WIKIMEDIA_URL, **(client_kw or {}))
            r = stream_sync(client, landing, name=NAME, transform=recentchange_row, key="event_id", seconds=seconds,
                            max_events=max_events, batch_events=2000, batch_seconds=10, planned_disconnect_at=seconds / 2)
            took = time.perf_counter() - t0
            state = json.loads((landing / "_stream_state.json").read_text()) if (landing / "_stream_state.json").exists() else {}
            rec.update(events=r.events, rows=r.written, duplicates=r.duplicates, filtered=r.filtered, bad_json=r.bad_json,
                       batches=r.batches, by_type=dict(Counter(r.by_type).most_common()), connects=client.stats.connects,
                       reconnects=client.stats.reconnects, reconnects_by_cause=dict(client.stats.by_cause),
                       resume_check=r.resume_check, events_per_second=round(r.events / max(took, 1e-9), 1),
                       lag_p50=_pct(r.lag, .5), lag_p95=_pct(r.lag, .95), drift=state.get("drift", []),
                       timeline=r.timeline(), disconnect_second=r.disconnect_second)
            if r.written:
                wh = Path(tmp) / "check.duckdb"
                ingest(Path(tmp) / "run", wh, load_contracts(contracts))
                with duckdb.connect(str(wh), read_only=True) as con:
                    rec["rows_loaded"] = con.execute(f"SELECT count(*) FROM current_{NAME}").fetchone()[0]
                    rec["rows_quarantined"] = con.execute("SELECT count(*) FROM quarantine WHERE source = ?", [NAME]).fetchone()[0]
                    rec["quarantine_reasons"] = dict(con.execute(
                        "SELECT reason, count(*) FROM quarantine WHERE source = ? GROUP BY 1 ORDER BY 2 DESC LIMIT 5", [NAME]).fetchall())
                    rec["wikis"] = con.execute(f"SELECT count(DISTINCT wiki) FROM current_{NAME}").fetchone()[0]
                # A cloud publish that fails must not cost the source its cursor: the sync is recorded as done
                # and the failure as publish_error, which alerts and turns the job red (main returns 1).
                try:
                    rec["published"] = _publish(wh, publish_to, rec["run_at"])
                except Exception as e:
                    rec["published"], rec["publish_error"] = None, f"{type(e).__name__}: {e}"[:400]
            rec["ok"] = True
    except Exception as e:                           # recorded, then re-raised so the job shows red
        rec["error"] = f"{type(e).__name__}: {e}"[:400]
        rec["seconds"] = round(time.perf_counter() - t0, 2)
        _append(out, rec)
        raise
    rec["seconds"] = round(time.perf_counter() - t0, 2)
    _append(out, rec)
    return rec


def _publish(warehouse: Path, target: str | None, run_at: str) -> dict | None:
    """To the cloud warehouse when a token is configured; otherwise None, and nothing is claimed."""
    from ..products.publish import Table, publish
    if target == "off":
        return None
    target = target or ("md:plumbline" if os.environ.get("MOTHERDUCK_TOKEN") else None)
    if not target:
        return None
    r = publish(warehouse, [Table(f"current_{NAME}", "event_id", "event_time")], target, run=run_at)
    return {"target": r["target"], **{k: v for k, v in r["tables"][0].items() if k != "table"}}


def _append(out: Path, rec: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "runs.jsonl", "a") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")


def history(out: Path = OUT, url: str | None = None, timeout: float = 4.0) -> list[dict]:
    from .open_data_daily import _parse
    if url:
        import urllib.request
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return _parse(resp.read().decode("utf-8", "replace"))
        except Exception:
            pass
    p = out / "runs.jsonl"
    return _parse(p.read_text()) if p.exists() else []


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seconds", type=float, default=120.0)
    a = ap.parse_args(argv)
    try:
        r = run(seconds=a.seconds)
    except Exception:
        traceback.print_exc()
        return 1
    print(json.dumps({k: v for k, v in r.items() if k not in ("dropped_fields", "timeline")}, indent=1))
    return 1 if r.get("publish_error") else 0


if __name__ == "__main__":
    sys.exit(main())
