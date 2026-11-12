"""Incremental, resumable sync of a public dataset from NYC Open Data (any Socrata dataset).

Data nobody here controls: the city adds and corrects rows every day, the schema can grow a column
without notice, the API throttles, and JSON rows leave out fields that are empty, so two pages of
the same dataset can have different keys.

What makes it safe (the store connector's rules, applied to a source we do not own):
- Incremental by the row's own `:updated_at`, minus an overlap, paged by a keyset on
  (:updated_at, :id). Offset paging is not stable here: a row corrected mid-sync moves to the end
  and shifts every later row back by one, so one row would be skipped.
- Retries: 429 honours Retry-After; 5xx and dropped or half-sent responses back off with jitter.
- Resumable: each page is written atomically, then the cursor; a crash resumes from the last cursor, and so does
  a run that stopped at its page budget (max_pages): the next run continues where it stopped, it does not start over.
- A publisher reload is skipped, not re-read: when one update time covers more than `reload_rows` rows (counted
  with one request), the run records it and moves past it. On 2026-11-10 NYC stamped 803,457 permits of
  rbx6-tga4 with one time; each run read the first 60 pages of that block, stopped at its budget, started over
  at the top the next time, and never reached the 2,438 real changes after it, while every run passed.
- A fixed column set per landing folder: the first sync records it. Fields a row leaves out are
  written empty; a column that appears later is reported as drift and not written, unless the reviewed
  contract already lists it (`accepted`): the API leaves out a field that is empty on every row of a page,
  so a real column can be missing from the first page. That happened to apt_condo_no_s on 2026-11-07, and
  every apartment number was dropped for five days although the contract had approved the column.
- Privacy: columns that name or contact a person (owner, applicant, phone, email...) are dropped
  before anything is written, the same rule the PLUTO download follows. Records of buildings stay.
- Provenance: every row keeps the city's row id and update time (source_row_id, source_updated_at);
  the loader records which delivery file and when.
- An app token, if set (SOCRATA_APP_TOKEN), raises the rate limit; it is never written anywhere.

    python -m plumbline.ingest.connectors.socrata ipu4-2q9a data/landing/permits --since 2026-10-01
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from .store_api import StoreClient, SyncStats, _atomic_write

DEFAULT_HOST = "https://data.cityofnewyork.us"
# People, not buildings: "street_name" is an address and stays; "owner_s_first_name" is a person and goes.
PERSONAL = re.compile(r"owner|applicant|permittee|filing_rep|superintendent|contact|signature|phone|e_?mail|ssn|"
                      r"license_?(no|num)|(first|last|middle|full|given|sur)_?name|^name$|business_name", re.I)
SYSTEM = {":id": "source_row_id", ":updated_at": "source_updated_at"}


class SocrataError(RuntimeError):
    pass


class SocrataClient(StoreClient):
    """The store client's retries and backoff; an optional app token instead of a required one."""

    def __init__(self, host: str = DEFAULT_HOST, app_token: str | None = None, **kw):
        super().__init__(host, app_token or "anonymous", **kw)
        self._app = app_token

    def _headers(self) -> dict:
        h = {"Accept": "application/json", "User-Agent": "plumbline-socrata/0.1 (portfolio project)"}
        if self._app:
            h["X-App-Token"] = self._app
        return h


def _ts(v: str) -> str:
    """Socrata timestamps come as 2026-11-05T12:00:00.000Z; SoQL compares them without the zone."""
    return v.rstrip("Z")


WINDOW_CAP = 200_000


def _minus(ts: str | None, d: timedelta) -> str:
    """A timestamp in the API's form, d earlier (string comparison then orders like the API does)."""
    if not ts:
        return ""
    return (datetime.fromisoformat(ts) - d).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


def _literal(v: str) -> str:
    """A full timestamp for a SoQL comparison, in the form the API returns ('2026-10-01T00:00:00.000').
    Accepts a date, a timestamp, or one ending in Z; anything else is refused before a request is made."""
    v = v.strip().rstrip("Z")
    try:
        d = datetime.fromisoformat(v)
    except ValueError:
        raise SocrataError(f"not a date or timestamp: {v!r}") from None
    return d.strftime("%Y-%m-%dT%H:%M:%S") + f".{d.microsecond // 1000:03d}"


def _query(dataset: str, cursor: tuple[str, str] | None, since: str | None, limit: int, after: str | None = None) -> str:
    if cursor:
        t, rid = cursor
        t = _literal(t)
        where = f":updated_at > '{t}' OR (:updated_at = '{t}' AND :id > '{rid}')"
    elif after:                                       # strictly past a skipped reload
        where = f":updated_at > '{_literal(after)}'"
    elif since:
        where = f":updated_at >= '{_literal(since)}'"
    else:
        where = None
    # The star must come first: NYC's API refused ":id,:updated_at,*" with HTTP 400 ("Star selections must
    # come at the start of the select-list"), checked against data.cityofnewyork.us on 2026-11-06.
    q = {"$select": "*, :id, :updated_at", "$order": ":updated_at,:id", "$limit": limit}
    if where:
        q["$where"] = where
    return f"/resource/{dataset}.json?" + urllib.parse.urlencode(q, quote_via=urllib.parse.quote)


def _flat(row: dict) -> dict:
    """Scalars only: nested values (map points, links) are dropped, not stringified."""
    out = {SYSTEM.get(k, k): v for k, v in row.items()
           if (k in SYSTEM or not k.startswith(":")) and not isinstance(v, (dict, list))}
    if "source_updated_at" in out:     # landed as the cursor compares it; Python 3.10 cannot parse a trailing Z
        out["source_updated_at"] = _ts(out["source_updated_at"])
    return out


def _to_csv(rows: list[dict], columns: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def _aggregate(client, dataset: str, select: str, where: str | None, stats) -> dict:
    q = {"$select": select} | ({"$where": where} if where else {})
    body, _ = client.get(f"/resource/{dataset}.json?" + urllib.parse.urlencode(q, quote_via=urllib.parse.quote), stats)
    return body[0] if isinstance(body, list) and body else {}


def rows_at(client, dataset: str, t: str, stats) -> int:
    """How many rows carry exactly this update time (one request)."""
    return int(_aggregate(client, dataset, "count(*)", f":updated_at = '{_literal(t)}'", stats).get("count", 0))


def source_newest(client, dataset: str, stats) -> str | None:
    """The newest update time the publisher has, to measure how far behind the landed data is."""
    v = _aggregate(client, dataset, "max(:updated_at)", None, stats).get("max_updated_at")
    return _ts(v) if v else None


def bulk_rewrite(stamps: dict, page_size: int, share: float = 0.9) -> dict | None:
    """Did one update time cover most of this sync? NYC's DOB permit issuance (ipu4-2q9a) stamped 3,898,188
    of its 3,991,259 rows with one time on 2026-11-05: the publisher reloaded the table, so "changed since
    the last sync" means nearly everything. Reported, so the cost is a decision, not a surprise; the loader's
    row hashes still store an unchanged row only once."""
    n = sum(stamps.values())
    if not n:
        return None
    t, k = max(stamps.items(), key=lambda kv: kv[1])
    return {"timestamp": t, "rows": k, "of": n, "share": round(k / n, 4)} if k >= 2 * page_size and k / n >= share else None


def sync_dataset(client: SocrataClient, dataset: str, landing_dir: str | Path, *, name: str, page_size: int = 1000,
                 since: str | None = None, overlap: timedelta = timedelta(minutes=5), max_pages: int | None = None,
                 after_page: Callable[[int], None] | None = None, reload_rows: int | None = 100_000,
                 accepted: set[str] | frozenset = frozenset()) -> SyncStats:
    """Fetch rows added or changed since the last sync into landing_dir as CSV pages."""
    landing = Path(landing_dir)
    landing.mkdir(parents=True, exist_ok=True)
    state_path = landing / "_sync_state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {
        "dataset": dataset, "watermark": None, "next_sync": 1, "columns": None, "dropped_personal": [], "drift": []}
    stats = SyncStats()
    stats.by_error = {}
    stats.reread = 0
    stats.capped = False
    stats.reload_skipped = None
    # Rows already landed inside the last overlap window, as "row id|version time". The overlap re-reads the
    # last few minutes to catch rows written late with an older time; NYC stamps a whole daily batch with one
    # time, so without this every run re-landed that entire batch and counted it as new.
    seen = set(state.get("window_ids") or [])
    if state.get("in_progress"):
        run = state["in_progress"]
        stats.resumed = True
    else:
        start = (datetime.fromisoformat(state["watermark"]) - overlap).strftime("%Y-%m-%dT%H:%M:%S") \
            if state["watermark"] else since
        run = {"sync_id": state["next_sync"], "page": 0, "since": start, "cursor": None, "max_seen": state["watermark"], "stamps": {},
               "recent": []}
    pages = 0
    while True:
        body, _ = client.get(_query(dataset, tuple(run["cursor"]) if run["cursor"] else None, run["since"], page_size,
                                    run.get("after")), stats)
        rows = [_flat(r) for r in (body if isinstance(body, list) else [])]
        run["page"] += 1
        pages += 1
        skip = None
        if reload_rows and len(rows) == page_size and len({r["source_updated_at"] for r in rows}) == 1:
            t = _ts(rows[0]["source_updated_at"])          # a full page under one time: how big is that block?
            if t not in run.setdefault("checked", []):
                run["checked"].append(t)
                n = rows_at(client, dataset, t, stats)
                if n > reload_rows:
                    skip = {"timestamp": t, "rows": n}
        if skip:                                       # a reload, not changes: record it and move past it
            run["skipped"] = skip
            run["cursor"], run["after"] = None, skip["timestamp"]
            run["max_seen"] = max(run["max_seen"] or skip["timestamp"], skip["timestamp"])
            stats.reload_skipped = skip
            rows = []
        if rows:
            if state["columns"] is None:          # the first page fixes the delivery's shape
                keys = sorted({k for r in rows for k in r} - set(SYSTEM.values()))
                state["dropped_personal"] = [k for k in keys if PERSONAL.search(k)]
                state["columns"] = ["source_row_id", "source_updated_at"] + [k for k in keys if not PERSONAL.search(k)]
            new = sorted({k for r in rows for k in r} - set(state["columns"]) - set(state["dropped_personal"])
                         - {k for k in {k for r in rows for k in r} if PERSONAL.search(k)})
            for k in new:
                if k in accepted:                       # approved in the contract: a column, not drift
                    state["columns"].append(k)
                    if k in state["drift"]:
                        state["drift"].remove(k)
                elif k not in state["drift"]:
                    state["drift"].append(k)
            fresh = [r for r in rows if f"{r['source_row_id']}|{_ts(r['source_updated_at'])}" not in seen]
            stats.reread += len(rows) - len(fresh)
            if fresh:
                _atomic_write(landing / f"{name}_s{run['sync_id']:04d}_p{run['page']:05d}.csv", _to_csv(fresh, state["columns"]))
            last = rows[-1]
            cursor = [_ts(last["source_updated_at"]), last["source_row_id"]]
            if run["cursor"] == cursor:                  # the API sent the same page again: never loop on it
                raise SocrataError(f"no progress past {cursor}: the API ignored the cursor; stopping without moving the watermark")
            run["cursor"] = cursor
            top = max(_ts(r["source_updated_at"]) for r in rows)
            run["max_seen"] = max(run["max_seen"] or top, top)
            stamps = run.setdefault("stamps", {})
            for r in rows:
                t = _ts(r["source_updated_at"])
                stamps[t] = stamps.get(t, 0) + 1
            edge = _minus(run["max_seen"], overlap)
            run["recent"] = [x for x in run.get("recent", []) if x[0] >= edge] + \
                [[_ts(r["source_updated_at"]), r["source_row_id"]] for r in rows if _ts(r["source_updated_at"]) >= edge]
            if len(run["recent"]) > WINDOW_CAP:          # a table reloaded under one time: too many to remember
                run["recent"], run["window_too_big"] = [], True
            if len(stamps) > 50:                       # keep the state small: only the largest groups matter
                run["stamps"] = dict(sorted(stamps.items(), key=lambda kv: -kv[1])[:50])
        stats.pages += 1
        stats.orders += len(fresh) if rows else 0  # rows landed this run, in the store's counter
        done = not skip and len(rows) < page_size                      # the source has nothing further
        capped = not done and max_pages is not None and pages >= max_pages
        state["in_progress"] = None if done else run                  # a capped run keeps its place for the next one
        stats.capped = capped
        stats.reload_skipped = stats.reload_skipped or run.get("skipped")
        if done:
            state["bulk_rewrite"] = bulk_rewrite(run.get("stamps", {}), page_size)
            state["watermark"] = run["max_seen"]
            edge = _minus(run["max_seen"], overlap) if run["max_seen"] else None
            if run.get("window_too_big"):
                state["window_ids"] = None
            else:                                       # what this run saw in the new window, plus what it skipped there
                keep = {f"{i}|{t}" for t, i in run.get("recent", []) if edge and t >= edge}
                keep |= {k for k in seen if edge and k.rsplit("|", 1)[1] >= edge}
                state["window_ids"] = sorted(keep)
            state["next_sync"] = run["sync_id"] + 1
            state["last_sync_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _atomic_write(state_path, json.dumps(state, indent=2))
        if after_page:
            after_page(run["page"])
        if done or capped:
            return stats


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dataset", help="Socrata dataset id, e.g. ipu4-2q9a (DOB permit issuance)")
    ap.add_argument("landing", help="landing folder, e.g. data/synth/seed7_n100000/sources/permits")
    ap.add_argument("--name", default=None, help="file prefix; defaults to the folder name")
    ap.add_argument("--since", default=None, help="first sync only: rows updated on or after this date")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--page-size", type=int, default=1000)
    ap.add_argument("--max-pages", type=int, default=None)
    a = ap.parse_args(argv)
    client = SocrataClient(a.host, os.environ.get("SOCRATA_APP_TOKEN"))
    st = sync_dataset(client, a.dataset, a.landing, name=a.name or Path(a.landing).name, page_size=a.page_size,
                      since=a.since, max_pages=a.max_pages)
    state = json.loads((Path(a.landing) / "_sync_state.json").read_text())
    print(json.dumps({"rows": st.orders, "pages": st.pages, "requests": st.requests, "retries": st.retries,
                      "resumed": st.resumed, "watermark": state["watermark"], "drift": state["drift"],
                      "dropped_personal": state["dropped_personal"]}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
