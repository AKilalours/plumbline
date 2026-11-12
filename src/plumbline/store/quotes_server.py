"""A local stand-in for quotes.toscrape.com, for tests: the same listing markup and Next links, plus the
things a polite scraper must handle. The quotes are made up.

`robots` is the robots.txt body (None: 404; an int: that status). `fail` is a list of status codes to send,
one per page request, before answering. Every request is logged with its path, time and User-Agent.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@dataclass
class Site:
    pages: int = 3
    per_page: int = 10
    robots: str | int | None = "User-agent: *\nCrawl-delay: 2\nDisallow: /login\n"
    fail: list[int] = field(default_factory=list)
    retry_after: str | None = None
    log: list[dict] = field(default_factory=list)

    def listing(self, n: int) -> str:
        qs = "".join(
            f'<div class="quote"><span class="text">“Made-up line {n}-{i}.”</span>'
            f'<span>by <small class="author">Author {(n * 7 + i) % 13}</small></span>'
            f'<div class="tags"><a class="tag" href="/tag/t{i % 3}/">t{i % 3}</a><a class="tag" href="/tag/x/">x</a></div></div>'
            for i in range(self.per_page))
        nxt = f'<li class="next"><a href="/page/{n + 1}/">Next →</a></li>' if n < self.pages else ""
        return f'<html><body><a href="/login">Login</a>{qs}<nav><ul class="pager">{nxt}</ul></nav></body></html>'


def serve(site: Site) -> tuple[ThreadingHTTPServer, str]:
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code: int, body: str = "", ctype="text/html; charset=utf-8", headers=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self):
            site.log.append({"path": self.path, "at": time.monotonic(), "agent": self.headers.get("User-Agent", ""),
                             "if_none_match": self.headers.get("If-None-Match")})
            if self.path == "/robots.txt":
                if site.robots is None:
                    return self._send(404, "not found")
                if isinstance(site.robots, int):
                    return self._send(site.robots, "error")
                return self._send(200, site.robots, "text/plain")
            if site.fail:
                code = site.fail.pop(0)
                return self._send(code, "slow down", headers={"Retry-After": site.retry_after} if site.retry_after else None)
            if self.path.startswith("/page/"):
                n = int(self.path.strip("/").split("/")[1])
                if not 1 <= n <= site.pages:
                    return self._send(404, "no such page")
                etag = f'"p{n}-{site.per_page}"'
                if self.headers.get("If-None-Match") == etag:
                    return self._send(304)
                return self._send(200, site.listing(n), headers={"ETag": etag})
            if self.path == "/login":
                return self._send(200, "<html>login</html>")
            self._send(404, "not found")

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"
