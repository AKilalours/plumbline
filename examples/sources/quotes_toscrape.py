"""Quotes from quotes.toscrape.com, a site built for people to practise scraping on: the polite-scraper example.

Reads each listing page, follows the "Next" link, and keeps robots.txt, the crawl delay and a named contact.
"""
import hashlib
from html.parser import HTMLParser

from plumbline.ingest.connectors.scrape import PoliteSource
from plumbline.kit import Page

START = "https://quotes.toscrape.com/page/1/"


class Quotes(PoliteSource):
    name, key, start = "quotes_toscrape", "quote_id", START
    contact = "https://github.com/AKilalours/plumbline"

    def pages(self, cursor):
        url = self.start
        while url:
            p = Listing()
            p.feed(self.get_html(url))
            rows = [{"quote_id": hashlib.sha1(f"{q['text']}|{q['author']}".encode()).hexdigest()[:16],
                     "text": q["text"], "author": q["author"], "tags": ",".join(q["tags"]), "page": url} for q in p.quotes]
            yield Page(rows, cursor="read")             # nothing to resume from: the site has no "changed since"
            url = p.next and requests_join(url, p.next)


class Listing(HTMLParser):
    """The three things on a listing page: quote text, author, tags; and the Next link."""

    def __init__(self):
        super().__init__()
        self.quotes, self.next, self._in = [], None, None

    def handle_starttag(self, tag, attrs):
        c = dict(attrs).get("class", "")
        if tag == "div" and c == "quote":
            self.quotes.append({"text": "", "author": "", "tags": []})
        elif self.quotes and (tag, c) in (("span", "text"), ("small", "author"), ("a", "tag")):
            self._in = c
        elif tag == "li" and c == "next":
            self._in = "next"
        elif tag == "a" and self._in == "next":
            self.next, self._in = dict(attrs).get("href"), None

    def handle_data(self, data):
        if self._in in ("text", "author"):
            self.quotes[-1][self._in] += data.strip()
        elif self._in == "tag":
            self.quotes[-1]["tags"].append(data.strip())

    def handle_endtag(self, tag):
        if tag in ("span", "small", "a") and self._in in ("text", "author", "tag"):
            self._in = None


def requests_join(base, href):
    from urllib.parse import urljoin
    return urljoin(base, href)
