"""The polite scraper: robots.txt decides, the crawl delay is kept, the site knows who we are, unchanged pages
are not downloaded again, and it all runs through the same kit as an API source (examples/sources/quotes_toscrape.py)."""
from pathlib import Path

import pytest

from plumbline import kit
from plumbline.ingest.connectors.scrape import Budget, Disallowed, PoliteSource
from plumbline.store.quotes_server import Site, serve

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "sources" / "quotes_toscrape.py"


@pytest.fixture
def site():
    s = Site()
    srv, url = serve(s)
    s.url = url
    yield s
    srv.shutdown()


class Clock:
    """Time that only moves when the scraper sleeps, so delays are checked exactly and cost nothing."""
    def __init__(self):
        self.t, self.slept = 1000.0, []

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


def quotes(site, clock=None, **attrs):
    cls = kit._load(str(EXAMPLE))
    cls.start = site.url + "/page/1/"
    for k, v in attrs.items():
        setattr(cls, k, v)

    def make():
        q = cls()
        c = clock or Clock()
        q._sleep, q._clock = c.sleep, (lambda: c.t)
        return q
    return make


def pages_requested(site):
    return [e["path"] for e in site.log if e["path"].startswith("/page/")]


def test_it_reads_every_listing_page_by_following_next(site):
    rows = [r for p in quotes(site)().pages(None) for r in p.rows]
    assert len(rows) == 30 and len({r["quote_id"] for r in rows}) == 30
    assert rows[0]["author"].startswith("Author ") and rows[0]["tags"] == "t0,x"
    assert pages_requested(site) == ["/page/1/", "/page/2/", "/page/3/"]


def test_robots_txt_is_read_first_and_a_disallowed_path_is_never_requested(site):
    q = quotes(site)()
    assert site.log == [] or site.log[0]["path"] == "/robots.txt"
    with pytest.raises(Disallowed, match="does not allow /login"):
        q.get_html(site.url + "/login")
    assert site.log[0]["path"] == "/robots.txt" and "/login" not in [e["path"] for e in site.log]


def test_the_crawl_delay_is_kept_between_requests(site):
    c = Clock()
    list(quotes(site, c)().pages(None))
    assert c.slept == [2.0, 2.0]                         # 3 pages, Crawl-delay: 2, nothing before the first


def test_without_a_crawl_delay_it_still_waits_its_minimum(site):
    site.robots = "User-agent: *\nDisallow:\n"
    c = Clock()
    list(quotes(site, c)().pages(None))
    assert c.slept == [1.0, 1.0]


def test_no_robots_txt_allows_everything_but_an_unreadable_one_allows_nothing(site):
    site.robots = None
    assert quotes(site)().allowed(site.url + "/login")
    site.robots = 503
    q = quotes(site)()
    with pytest.raises(Disallowed, match="could not be read"):
        q.get_html(site.url + "/page/1/")
    assert pages_requested(site) == []


def test_told_to_slow_down_it_waits_as_told_then_goes_slower_for_the_rest_of_the_run(site):
    site.fail, site.retry_after = [429], "30"
    c = Clock()
    list(quotes(site, c)().pages(None))
    assert c.slept[0] == 30.0 and c.slept[1:] == [4.0, 4.0]      # the 2 s delay doubled after the 429


def test_the_site_is_told_who_is_asking_and_how_to_reach_them(site):
    list(quotes(site)().pages(None))
    assert all("plumbline/1.0 (+https://github.com/AKilalours/plumbline)" == e["agent"] for e in site.log)

    class Anonymous(PoliteSource):
        name = "anon"
    with pytest.raises(ValueError, match="contact"):
        Anonymous()


def test_an_unchanged_page_asked_for_again_is_not_downloaded(site, tmp_path):
    make = quotes(site)
    a = make()
    a.state_dir = tmp_path
    assert a.get_html(site.url + "/page/2/", if_changed=True)
    b = make()
    b.state_dir = tmp_path
    b._seen = b._load_seen()                                       # what a new run reads from state_dir
    assert b.get_html(site.url + "/page/2/", if_changed=True) is None and b.not_modified == 1
    assert site.log[-1]["if_none_match"] == '"p2-10"'


def test_a_request_budget_stops_a_runaway_crawl(site):
    site.pages = 50
    with pytest.raises(Budget, match="used its 5 requests"):
        list(quotes(site, max_requests=5)().pages(None))
    assert len(pages_requested(site)) == 4                         # robots.txt was the fifth


def test_the_scraper_passes_the_kits_verify_like_any_source(site, tmp_path):
    make = quotes(site)
    contract = kit.draft(make(), out=tmp_path / "q.yaml", workdir=tmp_path / "d")
    checks = kit.verify(make, contract=contract, workdir=tmp_path / "v")
    assert all(ok for _, ok, _ in checks), checks
