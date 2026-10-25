"""Build the store's order database from a generator run.

It is a separate artifact (<run>/store/), seeded from its own stream, so building it never changes
the generator's files or fingerprint. Store customers are the people in the CRM. Each order carries
the customer details the shopper typed at checkout, which is how a real store API exposes them.

Planted signals, for later modeling (documented in docs/synthetic_data_card.md):
- order count per customer ~ Poisson(lambda), lambda = 1.5 x income band multiplier x loyalty (gamma, mean 1)
- order value ~ lognormal around $60 x (1 + 0.25 x (income band - 1))
- 6% of orders are refunded 3 to 30 days later, which bumps updated_at
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ..synth.damage import maybe_nickname, typo
from ..synth.world import DAY

ORDER_COLUMNS = ["order_id", "customer_email", "first_name", "last_name", "ship_street", "ship_zip",
                 "created_at", "updated_at", "financial_status", "total_price", "item_count", "currency"]


def _ts(day: np.datetime64, sec: int) -> str:
    return str(day.astype("datetime64[s]") + np.timedelta64(int(sec), "s"))


def build_store(run_dir: str | Path, seed: int | None = None) -> dict:
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    seed = manifest["config"]["seed"] if seed is None else seed
    rng = np.random.default_rng(np.random.SeedSequence([seed, 7_001]))
    truth = run_dir / "truth"
    people = pd.read_parquet(truth / "people.parquet").set_index("person_id")
    links = pd.read_parquet(truth / "record_links.parquet")
    addr = pd.read_parquet(truth / "address_history.parquet")
    start = np.datetime64(manifest["config"]["window_start"], "D")
    end = np.datetime64(manifest["config"]["window_end"], "D")

    customers = np.sort(links.loc[links["source"] == "crm", "person_id"].unique())
    n = len(customers)
    band = people.loc[customers, "income_band"].to_numpy().astype(int)
    loyalty = rng.gamma(2.0, 0.5, n)
    lam = 1.5 * (0.7 + 0.15 * band) * loyalty
    counts = rng.poisson(lam)
    use_work = rng.random(n) < 0.10
    work = people.loc[customers, "work_email"].to_numpy()
    email = np.where(use_work & pd.notna(work), work, people.loc[customers, "personal_email"].to_numpy())
    first = people.loc[customers, "first_name"].to_numpy()
    last = people.loc[customers, "last_name"].to_numpy()

    rows, links_out, damage = [], [], []
    addr = addr.assign(vf=pd.to_datetime(addr["valid_from"]).values.astype("datetime64[D]")).sort_values(
        ["person_id", "vf"], kind="mergesort")
    by_person = {p: (g["vf"].to_numpy(), g["street"].to_numpy(), g["zipcode"].to_numpy())
                 for p, g in addr.groupby("person_id", sort=False)}
    oid = 0
    for i in range(n):
        if counts[i] == 0:
            continue
        hist = by_person[customers[i]]
        days = np.sort(start + np.floor(rng.random(counts[i]) * ((end - start).astype(int) + 1)).astype(int) * DAY)
        for d in days:
            oid += 1
            order_id = f"KO{oid:08d}"
            j = int(np.searchsorted(hist[0], d, side="right")) - 1   # address in force on the order day
            f, nick = maybe_nickname(first[i], 0.05, rng)
            l = last[i]
            e = email[i]
            if rng.random() < 0.02:
                l = typo(l, rng)
                damage.append(("store", order_id, "last_name", "typo"))
            if nick:
                damage.append(("store", order_id, "first_name", "nickname"))
            created = _ts(d, int(rng.integers(6 * 3600, 23 * 3600)))
            refunded = rng.random() < 0.06
            if refunded:
                updated = _ts(min(d + int(rng.integers(3, 31)) * DAY, end), int(rng.integers(0, 86_400)))
                updated = max(updated, created)
            else:
                updated = created
            value = round(float(rng.lognormal(np.log(60 * (1 + 0.25 * (band[i] - 1))), 0.5)), 2)
            rows.append((order_id, e, f, l, hist[1][j], hist[2][j], created, updated,
                         "refunded" if refunded else "paid", value, int(rng.integers(1, 6)), "USD"))
            links_out.append(("store", order_id, customers[i]))
    orders = pd.DataFrame(rows, columns=ORDER_COLUMNS)
    out = run_dir / "store"
    out.mkdir(parents=True, exist_ok=True)
    orders.to_parquet(out / "orders.parquet", index=False)
    pd.DataFrame(links_out, columns=["source", "record_id", "person_id"]).to_parquet(out / "record_links.parquet", index=False)
    pd.DataFrame(damage, columns=["source", "record_id", "field", "kind"]).to_parquet(out / "damage_log.parquet", index=False)
    fp = hashlib.sha256(orders.to_csv(index=False, lineterminator="\n").encode()).hexdigest()[:16]
    info = {"orders": len(orders), "customers_with_orders": int((counts > 0).sum()), "fingerprint": fp,
            "refunded": int((orders["financial_status"] == "refunded").sum())}
    (out / "store_manifest.json").write_text(json.dumps(info, indent=2) + "\n")
    return info
