"""Real runs of the add-a-source kit and the polite scraper, recorded for the app to replay.

Nothing here is animated data: each scenario runs plumbline.kit against the local stand-in APIs
(store/usgs_server.py, store/quotes_server.py) and records what happened, page by page. The browser
replays the record; a test checks the record against a fresh run.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import duckdb

from .. import kit
from ..ingest.connectors.scrape import Disallowed, PoliteSource
from ..store import quotes_server, usgs_server

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "sources" / "kit" / "usgs_quakes.yaml"
EXAMPLE = ROOT / "examples" / "sources" / "usgs_quakes.py"
SIZE = 150                      # small pages, so a replay has several to watch


def _sources(url: str):
    base = kit._load(str(EXAMPLE))

    class Good(base):
        def pages(self, cursor, size=SIZE):
            yield from super().pages(cursor, size)

    class Careless(base):
        """The common mistake: the newest update seen so far, saved as the cursor on every page."""
        def pages(self, cursor, size=SIZE):
            since, offset = cursor or "2026-11-01T00:00:00", 1
            while True:
                body = self.get_json(self.url, format="geojson", updatedafter=since, orderby="time-asc", limit=size, offset=offset)
                rows = [{"event_id": f["id"], "updated_at": _iso(f["properties"]["updated"]), "magnitude": f["properties"]["mag"],
                         "place": f["properties"]["place"], "happened_at": _iso(f["properties"]["time"]),
                         "status": f["properties"]["status"]} for f in body["features"]]
                yield kit.Page(rows, cursor=max(r["updated_at"] for r in rows) if rows else None)
                if len(rows) < size:
                    return
                offset += size

    for c in (Good, Careless):
        c.url = url
    return Good, Careless


def _iso(ms):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


class _Rec:
    """Wraps a source so each page it yields is recorded: rows, the cursor it gave, the first and last id."""
    def __init__(self, src):
        self.src, self.pages = src, []

    def __call__(self):
        outer, inner = self, self.src

        class R(type(inner)):
            def pages(self, cursor):
                for p in type(inner).pages(self, cursor):
                    outer.pages.append({"rows": len(p.rows), "cursor": p.cursor,
                                        "first": p.rows[0]["event_id"] if p.rows else None})
                    yield p
        r = R()
        r._sleep = lambda _s: None
        return r


def _held(wd: Path) -> set[str]:
    with duckdb.connect(str(wd / "kit.duckdb"), read_only=True) as con:
        return {r[0] for r in con.execute("SELECT event_id FROM current_usgs_quakes").fetchall()}


def _crash_after_first(n):
    if n == 1:
        raise KeyboardInterrupt


def playground(n_events: int = 600) -> dict:
    """Four scenarios on one generated feed: a clean run, the same pages read again, a crash after page 1 then a
    resume, and the careless cursor through the same crash. Plus verify on the good and the careless source."""
    feed = usgs_server.Feed.generated(n_events)
    srv, url = usgs_server.serve(feed)
    try:
        Good, Careless = _sources(url)
        base = Path(tempfile.mkdtemp(prefix="kitdemo-"))
        out: dict = {"events": n_events, "page_size": SIZE}

        def scen(name, cls, crash=False, again=False):
            wd = base / name
            rec = _Rec(cls())
            t = time.perf_counter()
            steps = []
            if crash:
                try:
                    kit.run(rec(), workdir=wd, contract=CONTRACT, after_page=_crash_after_first)
                except KeyboardInterrupt:
                    pass
                steps.append({"label": "crash", "pages": list(rec.pages)})
                rec.pages.clear()
            r = kit.run(rec(), workdir=wd, contract=CONTRACT)
            steps.append({"label": "run", "pages": list(rec.pages), "loaded": r.rows_loaded, "cursor": r.cursor})
            if again:
                (wd / "run" / "sources" / "usgs_quakes" / "_kit_state.json").unlink()
                rec.pages.clear()
                r2 = kit.run(rec(), workdir=wd, contract=CONTRACT)
                steps.append({"label": "again", "pages": list(rec.pages), "loaded": r2.rows_loaded, "cursor": r2.cursor})
            held = _held(wd)
            return {"steps": steps, "held": len(held), "ms": round((time.perf_counter() - t) * 1000), "ids": held}

        clean = scen("clean", Good, again=True)
        crash = scen("crash", Good, crash=True)
        careless = scen("careless", Careless, crash=True)
        lost = sorted(clean["ids"] - careless["ids"])
        for s in (clean, crash, careless):
            s.pop("ids")
        out.update(clean=clean, crash=crash, careless=careless, lost=len(lost), lost_sample=lost[:6])
        out["stale"] = _stale_copy(feed, Good, base)
        out["verify"] = {name: [{"check": c, "ok": ok, "detail": d} for c, ok, d in
                                kit.verify(lambda cls=cls: _quiet(cls()), contract=CONTRACT, workdir=base / f"v-{name}",
                                           max_pages=n_events // SIZE + 2)]
                         for name, cls in (("good", Good), ("careless", Careless))}
        return out
    finally:
        srv.shutdown()


def _stale_copy(feed, Good, base: Path) -> dict:
    """The third engineer's finding, replayed: the API answers the first request from a copy that is behind, with
    an empty page flagged in a header. Without `stale_when` the run takes that as the end; with it, it asks again."""
    class Careful(Good):
        stale_when = {"X-Data-Out-Of-Date": "true"}

    out = {}
    for name, cls in (("naive", Good), ("careful", Careful)):
        feed.stale = 1
        rec = _Rec(cls())
        r = kit.run(rec(), workdir=base / f"stale-{name}", contract=CONTRACT)
        out[name] = {"pages": list(rec.pages), "loaded": r.rows_loaded, "stale_answers": r.stale_answers,
                     "held": len(_held(base / f"stale-{name}"))}
    feed.stale = 0
    return out


def _quiet(s):
    s._sleep = lambda _s: None
    return s


# ------------------------------------------------------------------ the polite scraper
ROBOTS = {"allows pages, blocks /login": "User-agent: *\nCrawl-delay: {d}\nDisallow: /login\n",
          "no robots.txt (404)": None, "cannot be read (503)": 503}


def scrape(delay: float = 2.0, robots: str = "allows pages, blocks /login", slow_down: bool = True, pages: int = 4) -> dict:
    """One real run of a PoliteSource against the stand-in site, on a clock that only moves when it waits.
    Returns every request the site received (time, path, status), the request robots.txt stopped before it
    was sent, and every wait with its length."""
    rb = ROBOTS[robots]
    now = [0.0]
    site = quotes_server.Site(pages=pages, robots=rb.format(d=delay) if isinstance(rb, str) else rb,
                              fail=[429] if slow_down else [], retry_after="6" if slow_down else None, clock=lambda: now[0])
    srv, url = quotes_server.serve(site)
    try:
        waits, blocked = [], []

        class Demo(PoliteSource):
            name, contact, min_delay = "quotes_demo", "https://github.com/AKilalours/plumbline", 1.0

            def pages(self, cursor):
                yield kit.Page([])

        s = Demo()
        s._clock = lambda: now[0]

        def sleep(x):
            waits.append({"at": round(now[0], 2), "secs": round(x, 2)})
            now[0] += x
        s._sleep = sleep
        outcome = "read every page"
        try:
            for n in range(1, pages + 1):
                s.get_html(f"{url}/page/{n}/")
                if n == 1:                                   # the page links to /login; robots.txt says no
                    try:
                        s.get_html(f"{url}/login")
                    except Disallowed:
                        blocked.append({"t": round(now[0], 2), "path": "/login"})
        except Disallowed:
            outcome = "stopped: robots.txt could not be read, so nothing is allowed"
        reqs = [{"t": round(e["at"], 2), "path": e["path"], "status": e.get("status")} for e in site.log]
        return {"requests": reqs, "blocked": blocked, "waits": waits, "seconds": round(now[0], 2), "outcome": outcome,
                "agent": s.user_agent, "delay": delay, "robots": robots, "slow_down": slow_down, "pages": pages}
    finally:
        srv.shutdown()
