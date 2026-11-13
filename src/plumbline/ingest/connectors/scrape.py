"""Scraping, politely: a kit Source that reads web pages only where the site allows it, as slowly as it asks.

    class Quotes(PoliteSource):
        name, key, contact = "quotes", "quote_id", "https://github.com/AKilalours/plumbline"
        def pages(self, cursor):
            url = START
            while url:
                html = self.get_html(url)              # robots.txt checked, delay kept, If-None-Match sent
                ...
                yield Page(rows)

What "politely" means here, each rule tested in tests/test_scrape.py:

1. **robots.txt decides** (RFC 9309). A path it disallows for our agent is never requested: `get_html`
   raises `Disallowed` before any request. A robots.txt that is missing (4xx) allows everything; one that
   cannot be read (5xx, timeout) disallows everything until it can, as the RFC says.
2. **One request at a time per host, never faster than asked.** `Crawl-delay` from robots.txt if present,
   else `min_delay` (1 s). A 429 or 503 with Retry-After is waited out, and the delay doubles for the rest
   of the run: the site said we were too fast.
3. **Say who we are.** The User-Agent names the project and a contact URL, so the site owner can reach us
   instead of blocking us. `contact` is required.
4. **Do not download what has not changed.** The ETag and Last-Modified of every page are kept between runs
   (in `state_dir`) and sent back with `get_html(url, if_changed=True)`; a 304 costs the site nothing and
   returns None. Not for listing pages: a 304 there would hide the link to the next page.
5. **A budget.** `max_requests` per run (default 200), so a parsing bug cannot turn into a crawl of the site.

Only scrape what the site's terms allow, and no personal data: the kit's contract and draft rules apply
here as everywhere.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from pathlib import Path

from ...kit import Source, SourceError, _retry_after


class Disallowed(SourceError):
    """robots.txt does not allow this URL for our agent."""


class Budget(SourceError):
    """The run used its request budget."""


class PoliteSource(Source):
    contact: str = ""                  # a URL or address the site owner can reach: required
    min_delay: float = 1.0
    max_requests: int = 200
    agent: str = "plumbline"

    def __init__(self, state_dir: Path | None = None) -> None:
        super().__init__()
        if not self.contact:
            raise ValueError(f"{type(self).__name__}.contact must say how a site owner can reach you")
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last: dict[str, float] = {}
        self._delay: dict[str, float] = {}
        self._clock = time.monotonic
        self.state_dir = state_dir
        self._seen = self._load_seen()
        self.not_modified = self.disallowed = 0

    @property
    def user_agent(self) -> str:
        return f"{self.agent}/1.0 (+{self.contact})"

    # ---------------------------------------------------------------- robots
    def _rules(self, url: str):
        u = urllib.parse.urlsplit(url)
        host = f"{u.scheme}://{u.netloc}"
        if host not in self._robots:
            rp = urllib.robotparser.RobotFileParser(host + "/robots.txt")
            try:
                self._requests += 1
                req = urllib.request.Request(host + "/robots.txt", headers={"User-Agent": self.user_agent})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    text = r.read().decode("utf-8", "replace")
                rp.parse(text.splitlines())
                rp._plumbline_delay = _crawl_delay(text, self.agent)
            except urllib.error.HTTPError as e:
                if 400 <= e.code < 500:
                    rp.parse([])                                  # no robots.txt: everything allowed
                else:
                    rp = None                                     # cannot read it: allowed nothing (RFC 9309 2.3.1.4)
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                rp = None
            self._robots[host] = rp
            d = getattr(rp, "_plumbline_delay", None) if rp else None
            self._delay[host] = max(float(d) if d else 0.0, self.min_delay)
        return host, self._robots[host]

    def allowed(self, url: str) -> bool:
        _, rp = self._rules(url)
        return bool(rp) and rp.can_fetch(self.agent, url)

    # ---------------------------------------------------------------- fetching
    def _wait(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            gap = self._delay[host] - (self._clock() - last)
            if gap > 0:
                self._sleep(gap)

    def get_html(self, url: str, *, if_changed: bool = False) -> str | None:
        """The page's text. With if_changed, None when it has not changed since the last run (304): use it for
        pages read for their own content, not for listing pages you need for the next link."""
        host, rp = self._rules(url)
        if not rp or not rp.can_fetch(self.agent, url):
            self.disallowed += 1
            raise Disallowed(f"{self.name}: robots.txt on {host} does not allow {urllib.parse.urlsplit(url).path}"
                             + ("" if rp else " (robots.txt could not be read, so nothing is allowed)"))
        seen = self._seen.get(url, {}) if if_changed else {}
        for attempt in range(self.max_retries + 1):
            if self._requests >= self.max_requests:
                raise Budget(f"{self.name}: used its {self.max_requests} requests for this run")
            self._wait(host)
            headers = {"User-Agent": self.user_agent, "Accept": "text/html"}
            if seen.get("etag"):
                headers["If-None-Match"] = seen["etag"]
            if seen.get("modified"):
                headers["If-Modified-Since"] = seen["modified"]
            self._requests += 1
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=self.timeout) as r:
                    self._last[host] = self._clock()
                    body = r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")
                    self._seen[url] = {k: v for k, v in {"etag": r.headers.get("ETag"),
                                                         "modified": r.headers.get("Last-Modified")}.items() if v}
                    self._save_seen()
                    return body
            except urllib.error.HTTPError as e:
                self._last[host] = self._clock()
                if e.code == 304:
                    self.not_modified += 1
                    return None
                if e.code not in (429, 503):
                    raise SourceError(f"{self.name}: HTTP {e.code} for {url}") from None
                self._delay[host] *= 2                            # told we were too fast: slower for the rest of the run
                wait = _retry_after(e.headers.get("Retry-After"))
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                self._last[host] = self._clock()
                wait = None
            if attempt == self.max_retries:
                raise SourceError(f"{self.name}: gave up on {url} after {attempt + 1} tries")
            self._retries += 1
            self._sleep(wait if wait is not None else self._delay[host])
        raise AssertionError("unreachable")

    # ---------------------------------------------------------------- conditional-request memory
    def _seen_path(self) -> Path | None:
        return self.state_dir / f"_{self.name}_seen.json" if self.state_dir else None

    def _load_seen(self) -> dict:
        p = self._seen_path()
        return json.loads(p.read_text()) if p and p.exists() else {}

    def _save_seen(self) -> None:
        p = self._seen_path()
        if p:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(self._seen, indent=1))


def _crawl_delay(text: str, agent: str) -> float | None:
    """Crawl-delay for `agent` (else for "*"), decimals included. The standard library's parser keeps only whole
    numbers, so "Crawl-delay: 2.5" came back as None and the scraper went faster than the site asked."""
    found: dict[str, float] = {}
    group: list[str] = []
    in_rules = False
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        k, v = (x.strip() for x in line.split(":", 1))
        k = k.lower()
        if k == "user-agent":
            if in_rules:
                group, in_rules = [], False
            group.append(v.lower())
        else:
            in_rules = True
            if k == "crawl-delay":
                try:
                    d = float(v)
                except ValueError:
                    continue
                for a in group:
                    found.setdefault(a, d)
    return found.get(agent.lower(), found.get("*"))
