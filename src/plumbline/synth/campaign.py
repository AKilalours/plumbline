"""How the invented people respond to a marketing email: the answer key for the campaign experiment.

The brand decides who gets the email (plumbline.experiments.design writes the send list from the
warehouse). This module plays the world: for every person on the list it knows, from the hidden
truth, how likely they were to buy in the 30 days anyway (p0) and how much the email changes that
(tau). It draws who buys, writes the store orders the brand would see, and keeps p0, tau and the
draws in truth/, where no model may look.

Planted effect, by what the person's real order history looked like on 2025-12-31:

| Segment | Buys anyway in 30 days (p0) | Effect of the email (tau) |
| --- | --- | --- |
| never ordered | 0.6% | +0.2 to +0.6 points, rising with income band |
| lapsed (last order 180+ days ago) | 2% | +4.5 points |
| cooling (90 to 180 days) | 4% | +3 points |
| active (last 90 days) | 8%, more for frequent buyers | 0; frequent buyers (4+ orders in 2025) -1 point: they were buying anyway, and the email annoys some |

Each person's tau is also multiplied by a hidden factor (lognormal, mean 1), so even a perfect model
of the segments cannot rank people perfectly. Seeded from its own stream; it never changes the
generator's or the store's files.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .damage import typo

SEND_DATE = "2026-01-06"
WINDOW_DAYS = 30
ORDER_COLUMNS = ["order_id", "customer_email", "first_name", "last_name", "created_at", "total_price", "currency"]


def segments(run_dir: Path, as_of: str = "2025-12-31") -> pd.DataFrame:
    """Each person's true order history on the eve of the campaign, from the store's answer key."""
    store = run_dir / "store"
    orders = pd.read_parquet(store / "orders.parquet", columns=["order_id", "created_at", "total_price"])
    links = pd.read_parquet(store / "record_links.parquet").rename(columns={"record_id": "order_id"})
    o = orders.merge(links[["order_id", "person_id"]], on="order_id")
    o["created_at"] = pd.to_datetime(o["created_at"]).astype("datetime64[ns]")
    t = pd.Timestamp(as_of)
    o = o[o.created_at <= t + pd.Timedelta(days=1)]
    g = o.groupby("person_id")
    out = pd.DataFrame({"last_order": g.created_at.max(),
                        "orders_last_year": o[o.created_at > t - pd.Timedelta(days=365)].groupby("person_id").size()})
    out["orders_last_year"] = out["orders_last_year"].fillna(0).astype(int)
    days = (t - out["last_order"]).dt.days
    out["segment"] = np.select([days <= 90, days <= 180], ["active", "cooling"], "lapsed")
    return out


def true_effect(seg: pd.Series, band: pd.Series, orders_last_year: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    seg = seg.fillna("never")
    band = band.fillna(3).astype(float)
    n = orders_last_year.fillna(0).astype(float)
    p0 = np.select([seg == "never", seg == "lapsed", seg == "cooling"], [0.006, 0.02, 0.04],
                   0.08 + 0.02 * np.minimum(np.maximum(n - 1, 0), 3))
    tau = np.select([seg == "never", seg == "lapsed", seg == "cooling"], [0.002 * band, 0.045, 0.03],
                    np.where(n >= 4, -0.01, 0.0))
    return p0, tau


def simulate_response(run_dir: str | Path, sends: pd.DataFrame, seed: int | None = None) -> dict:
    """sends: one row per inbox in the experiment, columns email and arm ('treatment' or 'holdout').

    An email reaches everyone who reads that inbox (a family shares one). A person the brand holds
    two records for can sit in two inboxes; they count as treated if either was sent the email.
    Writes campaign/orders.csv and campaign/bounces.csv (what the brand sees) and, in truth/, each
    person's chance of buying with and without the email and each inbox's owners."""
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    seed = manifest["config"]["seed"] if seed is None else seed
    rng = np.random.default_rng(np.random.SeedSequence([seed, 7_002]))
    people = pd.read_parquet(run_dir / "truth" / "people.parquet",
                             columns=["person_id", "first_name", "last_name", "personal_email", "work_email", "income_band"])
    by_email = pd.concat([people[["person_id", "personal_email"]].rename(columns={"personal_email": "email"}),
                          people[["person_id", "work_email"]].rename(columns={"work_email": "email"})]).dropna()
    by_email = by_email.assign(email=by_email.email.str.lower()).drop_duplicates()
    sends = sends[["email", "arm"]].drop_duplicates("email").sort_values("email", kind="mergesort")
    reach = sends.merge(by_email, on="email", how="left")
    nobody = reach.person_id.isna()
    bounced = reach.loc[nobody & (reach.arm == "treatment"), ["email"]]   # the holdout was sent nothing
    owners = reach[~nobody].sort_values(["person_id", "email"], kind="mergesort")
    g = owners.groupby("person_id", sort=True)
    person = pd.DataFrame({"treated": g.arm.agg(lambda a: bool((a == "treatment").any())),
                           "inboxes": g.size(),
                           "first_email": g.email.first(),
                           "treated_email": owners[owners.arm == "treatment"].groupby("person_id").email.first()})
    person["in_both_arms"] = g.arm.nunique() > 1
    person = person.join(people.set_index("person_id")).join(segments(run_dir))
    p0, tau = true_effect(person.segment, person.income_band, person.orders_last_year)
    tau = tau * rng.lognormal(-0.5 * 0.6 ** 2, 0.6, len(person))      # hidden, mean 1: no model sees it
    treated = person.treated.to_numpy()
    u = rng.random(len(person))
    # one uniform decides both worlds, so "would have bought anyway" is defined for everyone
    if_treated, if_not = u < np.clip(p0 + tau, 0, 1), u < np.clip(p0, 0, 1)
    bought = np.where(treated, if_treated, if_not)

    rows, start, oid = [], np.datetime64(SEND_DATE, "s"), 0
    for i in np.flatnonzero(bought):
        r = person.iloc[i]
        sent_to = r.treated_email if r.treated and isinstance(r.treated_email, str) else r.first_email
        for _ in range(1 + int(rng.random() < 0.15)):                 # some buy twice
            oid += 1
            email, x = sent_to, rng.random()
            other = r.work_email if email == r.personal_email else r.personal_email
            if x < 0.10 and isinstance(other, str):
                email = other                                         # checked out with their other address
            elif x < 0.13:
                user, dom = email.split("@", 1)
                email = typo(user, rng) + "@" + dom                   # typed it wrong: unattributable
            t = start + np.timedelta64(int(rng.integers(3600, WINDOW_DAYS * 86_400)), "s")
            band = int(r.income_band) if pd.notna(r.income_band) else 3
            rows.append((f"KC{oid:07d}", email, r.first_name, r.last_name, str(t),
                         round(float(rng.lognormal(np.log(60 * (1 + 0.25 * (band - 1))), 0.5)), 2), "USD"))
    out = run_dir / "campaign"
    out.mkdir(parents=True, exist_ok=True)
    orders = pd.DataFrame(rows, columns=ORDER_COLUMNS)
    orders.to_csv(out / "orders.csv", index=False)
    bounced.to_csv(out / "bounces.csv", index=False)
    pd.DataFrame({"person_id": person.index, "treated": treated, "segment": person.segment.fillna("never").to_numpy(),
                  "p0": p0, "tau": tau, "bought": bought, "bought_if_treated": if_treated, "bought_if_not": if_not,
                  "in_both_arms": person.in_both_arms.to_numpy()}).to_parquet(run_dir / "truth" / "campaign_truth.parquet", index=False)
    owners[["email", "person_id"]].to_parquet(run_dir / "truth" / "campaign_inboxes.parquet", index=False)
    fp = hashlib.sha256(orders.to_csv(index=False, lineterminator="\n").encode()).hexdigest()[:16]
    info = {"inboxes": len(sends), "inboxes_that_reach_nobody": int(nobody.sum()), "bounced": len(bounced),
            "people_reached": len(person), "people_in_both_arms": int(person.in_both_arms.sum()),
            "orders": len(orders), "fingerprint": fp}
    (out / "campaign_manifest.json").write_text(json.dumps(info, indent=2) + "\n")
    return info
