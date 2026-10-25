"""python -m plumbline.store build|sync

build  writes <run>/store/orders.parquet (+ truth links) from the synthetic world.
sync   starts the mock store with faults on a local port and pulls it with the real connector into
       <run>/sources/store/, so the next `python -m plumbline.ingest` loads the orders.
       The token comes from STORE_API_TOKEN; for the local mock any value works.
"""
from __future__ import annotations

import argparse
import os
import secrets
import time

from ..ingest.connectors.store_api import StoreClient, sync_orders
from .db import build_store
from .server import Faults, MockStore

DEFAULT_RUN = "data/synth/seed7_n100000"


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="python -m plumbline.store")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("--run-dir", default=DEFAULT_RUN)
    s = sub.add_parser("sync"); s.add_argument("--run-dir", default=DEFAULT_RUN)
    s.add_argument("--faults", choices=["none", "rough"], default="rough")
    s.add_argument("--page-size", type=int, default=250)
    a = ap.parse_args(argv)
    if a.cmd == "build":
        m = build_store(a.run_dir)
        print(f"orders={m['orders']} customers_with_orders={m['customers_with_orders']} fingerprint={m['fingerprint']}")
        return
    token = os.environ.get("STORE_API_TOKEN") or secrets.token_urlsafe(16)
    f = Faults(p_429=0.10, p_503=0.05, p_drop=0.05, retry_after=1, refund_every=20, seed=1) if a.faults == "rough" else Faults()
    t0 = time.time()
    with MockStore.from_run(a.run_dir, token, f) as store:
        # Retry-After is honoured as real seconds here; in tests sleep is faked.
        st = sync_orders(StoreClient(store.url, token, base_delay=0.05), f"{a.run_dir}/sources/store", page_size=a.page_size)
        print(f"pages={st.pages} orders={st.orders} requests={st.requests} retries={st.retries} "
              f"by_error={st.by_error} refunds_during_sync={len(store.state.mutations)} "
              f"waited={st.waited_seconds:.1f}s elapsed={time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
