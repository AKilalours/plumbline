"""Store API connector contract: under any mix of faults, the warehouse ends up with exactly the
orders the store holds, each in its latest state, and the token never touches disk."""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import timedelta
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from plumbline.ingest.checks import check_invariants
from plumbline.ingest.connectors.store_api import AuthError, StoreAPIError, StoreClient, sync_orders
from plumbline.ingest.contracts import load_contracts
from plumbline.ingest.engine import ingest
from plumbline.store.db import build_store
from plumbline.store.server import Faults, MockStore
from plumbline.synth.config import SynthConfig
from plumbline.synth.generate import generate
from plumbline.synth.reference import load_reference

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = load_contracts(ROOT / "sources")
TOKEN = "test-token-not-a-secret-3f9a"


@pytest.fixture(scope="module")
def base_run(tmp_path_factory):
    d = tmp_path_factory.mktemp("synth") / "run"
    generate(load_reference(ROOT / "tests" / "fixtures" / "reference"), SynthConfig(seed=21, n_people=1500), d)
    build_store(d)
    return d


@pytest.fixture
def run(base_run, tmp_path):
    d = tmp_path / "run"
    shutil.copytree(base_run, d)
    return d


def _orders(run):
    return pd.read_parquet(run / "store" / "orders.parquet")


def _client(store, sleeps=None, **kw):
    return StoreClient(store.url, TOKEN, sleep=(sleeps.append if sleeps is not None else lambda s: None),
                       jitter_seed=0, **kw)


def _landing(run):
    return run / "sources" / "store"


def _current(run, wh):
    ingest(run, wh, CONTRACTS)
    with duckdb.connect(str(wh)) as con:
        return dict(con.execute("SELECT order_id, financial_status || '|' || strftime(updated_at, '%Y-%m-%dT%H:%M:%S') "
                                "FROM current_store").fetchall())


def _deleted_hashes(run):
    priv = pd.concat(pd.read_csv(p, dtype=str) for p in (run / "sources" / "privacy").glob("*.csv"))
    return set(priv.email_sha256.dropna())


def _h(email):
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


def _expected(orders_by_id, run):
    """What the warehouse must hold: every order the store has, latest state, minus deleted customers."""
    gone = _deleted_hashes(run)
    return {k: f'{v["financial_status"]}|{v["updated_at"]}' for k, v in orders_by_id.items()
            if _h(v["customer_email"]) not in gone}


def test_build_store_is_reproducible_and_links_to_people(run):
    a = build_store(run)
    first = (run / "store" / "orders.parquet").read_bytes()
    b = build_store(run)
    assert a["fingerprint"] == b["fingerprint"] and first == (run / "store" / "orders.parquet").read_bytes()
    o, links = _orders(run), pd.read_parquet(run / "store" / "record_links.parquet")
    assert len(o) > 500 and o.order_id.is_unique
    assert set(o.order_id) == set(links.query("source == 'store'").record_id)
    assert o.customer_email.str.endswith((".test", ".example")).all()


def test_clean_sync_lands_every_order_once(run, tmp_path):
    with MockStore(_orders(run), TOKEN) as s:
        st = sync_orders(_client(s), _landing(run), page_size=100)
        expected = _expected(s.state.orders, run)
    assert st.orders == len(s.state.orders) > len(expected) and st.retries == 0
    assert _current(run, tmp_path / "w.duckdb") == expected


def test_faults_and_mid_sync_refunds_still_converge(run, tmp_path):
    sleeps = []
    f = Faults(p_429=0.15, p_503=0.1, p_drop=0.1, retry_after=2, refund_every=5, seed=3)
    with MockStore(_orders(run), TOKEN, f) as s:
        st = sync_orders(_client(s, sleeps), _landing(run), page_size=50)
        assert s.state.mutations, "the test must actually move orders between pages"
        expected = _expected(s.state.orders, run)
    assert st.retries > 0 and st.throttled > 0
    assert 2.0 in sleeps, "429 must wait the server's Retry-After"
    cur = _current(run, tmp_path / "w.duckdb")
    assert cur == expected
    with duckdb.connect(str(tmp_path / "w.duckdb")) as con:
        assert {k: v for k, v in check_invariants(con, CONTRACTS).items() if v} == {}


def test_crash_between_pages_resumes_without_loss(run, tmp_path):
    with MockStore(_orders(run), TOKEN) as s:
        def crash(page):
            if page == 3:
                raise KeyboardInterrupt
        with pytest.raises(KeyboardInterrupt):
            sync_orders(_client(s), _landing(run), page_size=100, after_page=crash)
        state = json.loads((_landing(run) / "_sync_state.json").read_text())
        assert state["watermark"] is None and state["in_progress"]["page"] == 3
        st = sync_orders(_client(s), _landing(run), page_size=100)
        expected = _expected(s.state.orders, run)
    assert st.resumed and st.pages > 0
    assert _current(run, tmp_path / "w.duckdb") == expected


def test_second_sync_only_fetches_changes(run, tmp_path):
    with MockStore(_orders(run), TOKEN) as s:
        first = sync_orders(_client(s), _landing(run), page_size=250)
        k = sorted(s.state.orders)[0]
        latest = max(v["updated_at"] for v in s.state.orders.values())
        s.state.update(k, financial_status="refunded",
                       updated_at=(pd.Timestamp(latest) + pd.Timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S"))
        second = sync_orders(_client(s), _landing(run), page_size=250, overlap=timedelta(minutes=5))
        expected = _expected(s.state.orders, run)
    assert second.orders < 50 < first.orders, "incremental sync must not re-pull the whole store"
    cur = _current(run, tmp_path / "w.duckdb")
    assert cur == expected and cur[k].startswith("refunded")


def test_bad_token_stops_immediately(run):
    with MockStore(_orders(run), TOKEN) as s:
        c = StoreClient(s.url, "wrong", sleep=lambda x: None)
        with pytest.raises(AuthError):
            sync_orders(c, _landing(run))
        assert s.state.requests == 0  # 401 happens before counting; one attempt, no retries
    assert not list(_landing(run).glob("*.csv"))


def test_gives_up_and_keeps_old_watermark(run):
    with MockStore(_orders(run), TOKEN, Faults(p_503=1.0)) as s:
        with pytest.raises(StoreAPIError, match="gave up"):
            sync_orders(_client(s, max_retries=3), _landing(run))
    state_path = _landing(run) / "_sync_state.json"
    assert not state_path.exists() or json.loads(state_path.read_text())["watermark"] is None


def test_token_never_written_anywhere(run, tmp_path):
    with MockStore(_orders(run), TOKEN, Faults(p_429=0.2, seed=1)) as s:
        c = _client(s)
        sync_orders(c, _landing(run), page_size=100)
        assert TOKEN not in repr(c)
    wh = tmp_path / "w.duckdb"
    ingest(run, wh, CONTRACTS)
    for p in list(_landing(run).iterdir()) + [wh]:
        assert TOKEN.encode() not in p.read_bytes(), p.name


def test_deleted_customers_orders_are_suppressed(run, tmp_path):
    # Pick store customers who also asked to be deleted in the privacy feed.
    o = _orders(run)
    hit = o[o.customer_email.map(_h).isin(_deleted_hashes(run))]
    assert len(hit) > 0, "fixture must include at least one deleted store customer"
    with MockStore(o, TOKEN) as s:
        sync_orders(_client(s), _landing(run), page_size=250)
    cur = _current(run, tmp_path / "w.duckdb")
    assert not set(hit.order_id) & set(cur)
    assert len(cur) == len(o) - len(hit)


def test_change_in_the_same_second_as_the_watermark_is_not_lost(run, tmp_path):
    """Two writes in one second, the second committed after our read: a strict 'newer than
    watermark' query would miss it forever. The overlap window is what catches it."""
    with MockStore(_orders(run), TOKEN) as s:
        sync_orders(_client(s), _landing(run), page_size=250)
        wm = json.loads((_landing(run) / "_sync_state.json").read_text())["watermark"]
        k = next(k for k, v in sorted(s.state.orders.items()) if v["updated_at"] < wm and v["financial_status"] == "paid"
                 and _h(v["customer_email"]) not in _deleted_hashes(run))
        s.state.update(k, financial_status="refunded", updated_at=wm)
        sync_orders(_client(s), _landing(run), page_size=250)
        expected = _expected(s.state.orders, run)
    cur = _current(run, tmp_path / "w.duckdb")
    assert cur[k] == f"refunded|{wm}" and cur == expected


def test_indexed_paging_returns_exactly_what_the_slow_version_did():
    """Regression for speed, checked for sameness: pages used to re-sort every order, so a sync of
    186,000 orders spent 537 s in the mock store. The index must change nothing but the time."""
    import random
    import time

    from plumbline.store.server import Faults, StoreState
    rnd = random.Random(3)
    base = pd.Timestamp("2025-01-01")
    rows = [{"order_id": f"KO{i:08d}", "updated_at": (base + pd.Timedelta(seconds=rnd.randrange(0, 86_400 * 30))).strftime("%Y-%m-%dT%H:%M:%S"),
             "financial_status": "paid", "total_price": 1.0} for i in range(3000)]
    for r in rows[::7]:
        r["updated_at"] = rows[0]["updated_at"]                       # many ties on the timestamp
    st = StoreState(pd.DataFrame(rows), "t", Faults(refund_every=3, seed=1))
    for since in ("", "2025-01-10T00:00:00", "2025-02-15T00:00:00"):
        cur, seen = None, 0
        while True:
            st.requests += 1
            st._maybe_mutate()                                         # refunds move orders to the end, mid-sync
            fast, slow = st.page(since, 97, cur), st.page_reference(since, 97, cur)
            assert fast == slow
            seen += len(fast[0])
            cur = fast[1]
            if cur is None:
                break
    big = StoreState(pd.DataFrame([{**rows[0], "order_id": f"KO{i:08d}",
                                    "updated_at": (base + pd.Timedelta(seconds=i)).strftime("%Y-%m-%dT%H:%M:%S")}
                                   for i in range(60_000)]), "t", Faults())
    t, cur = time.time(), None
    while True:
        _, cur = big.page("", 250, cur)
        if cur is None:
            break
    assert time.time() - t < 5, "paging 60,000 orders should take well under a second"
