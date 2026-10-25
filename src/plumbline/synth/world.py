"""The hidden truth: households, people, where they live, where they work, what happens to them.

Nothing in this module is ever shown to the pipeline. Sources observe it (with damage)
in emit.py; evaluation compares the pipeline's output back against it.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import names as N
from .config import SynthConfig
from .reference import Reference

DAY = np.timedelta64(1, "D")  # always move dates by an explicit unit; bare ints are deprecated in numpy
INCOME_BANDS = np.array([35_000, 60_000, 100_000, 150_000, 250_000])  # bands 1..6


def d64(x) -> np.datetime64:
    return np.datetime64(x, "D")


def rand_dates(rng: np.random.Generator, n: int, lo, hi) -> np.ndarray:
    """Uniform whole days in [lo, hi]. lo and hi may be scalars or arrays of datetime64[D]."""
    lo = np.asarray(lo, dtype="datetime64[D]")
    hi = np.asarray(hi, dtype="datetime64[D]")
    span = np.maximum((hi - lo).astype(int), 0)
    off = np.floor(rng.random(n) * (span + 1)).astype(int)
    return lo + off.astype("timedelta64[D]")


def income_band(x) -> np.ndarray:
    return np.searchsorted(INCOME_BANDS, np.asarray(x), side="right") + 1


def _apt(rng: np.random.Generator, units: np.ndarray) -> np.ndarray:
    r = rng.integers(0, 1 << 30, size=len(units))
    return np.where(units > 1, r % np.clip(units, 1, 400) + 1, 0)


def work_email(first: str, last: str, employer: str) -> str:
    return f"{N.slug(first)}.{N.slug(last)}@{N.slug(employer)}.example"


@dataclass
class World:
    people: pd.DataFrame          # current truth per person, as of window_end
    households: pd.DataFrame
    address_history: pd.DataFrame  # person_id, valid_from, bbl, street, zipcode, unitsres, owner
    job_history: pd.DataFrame      # person_id, valid_from, employer, title, work_email
    events: pd.DataFrame           # event_id, person_id, household_id, event_type, event_date, detail


def build_world(ref: Reference, cfg: SynthConfig, rngs: dict) -> World:
    lots = ref.lots
    start, end = d64(cfg.window_start), d64(cfg.window_end)
    n = cfg.n_people
    units_all = lots["unitsres"].to_numpy()
    lot_w = np.minimum(units_all, 50).astype(float)
    lot_p = lot_w / lot_w.sum()

    # ---------- households
    rh = rngs["households"]
    draws = rh.choice(np.array([1, 2, 3, 4]), size=n + 10, p=[0.32, 0.33, 0.17, 0.18])
    cs = np.cumsum(draws)
    k = int(np.searchsorted(cs, n)) + 1
    sizes = draws[:k].copy()
    sizes[-1] = n - (cs[k - 2] if k >= 2 else 0)
    lot0 = rh.choice(len(lots), size=k, p=lot_p)
    apt0 = _apt(rh, units_all[lot0])
    med = lots["median_income"].to_numpy()[lot0]
    hh_income = np.round(rh.lognormal(np.log(med), 0.55), -2)
    owner0 = (units_all[lot0] <= 2) & (rh.random(k) < 0.6)
    head_idx = np.cumsum(sizes) - sizes

    # ---------- people
    rp = rngs["people"]
    hh = np.repeat(np.arange(k), sizes)
    pos = np.arange(n) - np.repeat(head_idx, sizes)
    sex = np.where(rp.random(n) < 0.5, "F", "M")
    head_age = rp.integers(25, 79, size=k)[hh]
    age = np.where(
        pos == 0, head_age,
        np.where(pos == 1, np.clip(np.round(head_age + rp.normal(0, 5, n)), 18, 90),
                 np.where(head_age >= 45, rp.integers(18, 31, n), rp.integers(18, 80, n)))).astype(int)
    dob = (start - (age * 365.25).astype(int).astype("timedelta64[D]")
           - rp.integers(0, 365, n).astype("timedelta64[D]"))

    rn = rngs["names"]
    f_pool = N.sample_weighted(rn, N.FEMALE_FIRST, n)
    m_pool = N.sample_weighted(rn, N.MALE_FIRST, n)
    first = np.where(sex == "F", f_pool, m_pool)
    hh_last = N.sample_weighted(rn, N.LAST, k)
    own_last = N.sample_weighted(rn, N.LAST, n)
    last = np.where((pos == 0) | (rn.random(n) < 0.8), hh_last[hh], own_last)

    # ---------- contacts (reserved domains and the fictional 555 area code only)
    rc = rngs["contacts"]
    phone = np.array([f"555{d:07d}" for d in rc.choice(10_000_000, size=n, replace=False)], dtype=object)
    pattern = rc.integers(0, 6, n)
    digits = rc.integers(1, 100, n)
    dom = N.PERSONAL_DOMAINS[rc.integers(0, len(N.PERSONAL_DOMAINS), n)]
    share = (pos >= 1) & (rc.random(n) < 0.05)   # a shared household inbox
    emails: list[str] = []
    seen: set[str] = set()
    for i in range(n):
        f, l = N.slug(first[i]), N.slug(last[i])
        local = [f"{f}.{l}", f"{f}{l}", f"{f[:1]}{l}", f"{f}_{l}", f"{f}.{l}{digits[i]}", f"{l}.{f}"][pattern[i]]
        e = f"{local}@{dom[i]}"
        bump = 0
        while e in seen:
            bump += 1
            e = f"{local}{i}{bump if bump > 1 else ''}@{dom[i]}"
        seen.add(e)
        emails.append(e)
    personal_email = np.array(emails, dtype=object)
    personal_email = np.where(share, personal_email[head_idx[hh]], personal_email)

    # ---------- jobs
    rj = rngs["jobs"]
    employers = N.employer_pool(rj, max(200, n // 40))
    employed = rj.random(n) < np.where(age < 67, 0.85, 0.20)
    emp0 = rj.integers(0, len(employers), n)
    title0 = rj.integers(0, len(N.JOBS), n)
    changed = employed & (rj.random(n) < cfg.job_change_rate)
    job_date = rand_dates(rj, n, start + 30 * DAY, end)
    emp1 = (emp0 + rj.integers(1, len(employers), n)) % len(employers)
    title1 = np.where(rj.random(n) < 0.5, title0, rj.integers(0, len(N.JOBS), n))

    person_id = np.array([f"P{i:08d}" for i in range(n)], dtype=object)
    household_id = np.array([f"H{j:08d}" for j in range(k)], dtype=object)

    jh0 = pd.DataFrame({"person_id": person_id[employed], "valid_from": start,
                        "employer": employers[emp0[employed]], "title": N.JOBS[title0[employed]]})
    jh1 = pd.DataFrame({"person_id": person_id[changed], "valid_from": job_date[changed],
                        "employer": employers[emp1[changed]], "title": N.JOBS[title1[changed]]})
    job_history = pd.concat([jh0, jh1], ignore_index=True)
    fl = pd.DataFrame({"person_id": person_id, "first": first, "last": last})
    job_history = job_history.merge(fl, on="person_id", how="left")
    job_history["work_email"] = [work_email(a, b, c) for a, b, c in
                                 zip(job_history["first"], job_history["last"], job_history["employer"])]
    job_history = job_history.drop(columns=["first", "last"])

    # ---------- household moves and home purchases (planted signals, documented in the data card)
    re_ = rngs["events"]
    city_med = float(np.median(hh_income))
    income_mult = np.clip(np.exp(1.2 * (np.log(hh_income) - np.log(city_med))), 0.3, 3.0)
    hage = age[head_idx]
    age_mult = np.where((hage >= 28) & (hage <= 45), 1.6, 0.6)
    renter_mult = np.where(owner0, 0.4, 1.5)
    hjob = changed[head_idx]
    job_mult = np.where(hjob, 1.5, 1.0)
    p_buy = np.clip(cfg.home_purchase_base_rate * income_mult * age_mult * renter_mult * job_mult, 0, 0.5)
    purchase = re_.random(k) < p_buy
    lo = np.where(hjob, job_date[head_idx] + 30 * DAY, start + 30 * DAY)
    lo = np.minimum(lo, end)
    purchase_date = rand_dates(re_, k, lo, end)
    move = (re_.random(k) < cfg.move_rate) & ~purchase
    move_date = rand_dates(re_, k, start + 30 * DAY, end)
    event_date = np.where(purchase, purchase_date, move_date)
    moved = purchase | move
    small = np.flatnonzero(units_all <= 2)
    lot_buy = small[re_.integers(0, len(small), k)] if len(small) else lot0
    lot_rent = re_.choice(len(lots), size=k, p=lot_p)
    lot1 = np.where(purchase, lot_buy, lot_rent)
    apt1 = _apt(re_, units_all[lot1])
    owner1 = np.where(purchase, True, (units_all[lot1] <= 2) & (re_.random(k) < 0.3))

    def lot_rows(hids, valid_from, lot, apt, owner):
        sub = lots.iloc[lot]
        street = [a if u == 0 else f"{a} APT {u}" for a, u in zip(sub["address"].to_numpy(), apt)]
        return pd.DataFrame({"household_id": hids, "valid_from": valid_from, "bbl": sub["bbl"].to_numpy(),
                             "street": street, "zipcode": sub["zipcode"].to_numpy(),
                             "unitsres": sub["unitsres"].to_numpy(), "owner": owner})

    hh_hist = pd.concat([
        lot_rows(household_id, start, lot0, apt0, owner0),
        lot_rows(household_id[moved], event_date[moved], lot1[moved], apt1[moved], owner1[moved]),
    ], ignore_index=True)
    p2h = pd.DataFrame({"person_id": person_id, "household_id": household_id[hh]})
    address_history = p2h.merge(hh_hist, on="household_id", how="inner").sort_values(
        ["person_id", "valid_from"], kind="mergesort").reset_index(drop=True)

    # ---------- events (per person)
    members = p2h.assign(h=hh)
    ev = []
    mp = members[purchase[members["h"].to_numpy()]]
    ev.append(pd.DataFrame({"person_id": mp["person_id"], "household_id": mp["household_id"],
                            "event_type": "home_purchase", "event_date": event_date[mp["h"].to_numpy()]}))
    mm = members[moved[members["h"].to_numpy()]]
    ev.append(pd.DataFrame({"person_id": mm["person_id"], "household_id": mm["household_id"],
                            "event_type": "move", "event_date": event_date[mm["h"].to_numpy()]}))
    ev.append(pd.DataFrame({"person_id": person_id[changed], "household_id": household_id[hh[changed]],
                            "event_type": "job_change", "event_date": job_date[changed]}))
    events = pd.concat(ev, ignore_index=True).sort_values(
        ["event_date", "person_id", "event_type"], kind="mergesort").reset_index(drop=True)
    events.insert(0, "event_id", [f"EVT{i:08d}" for i in range(len(events))])

    households = pd.DataFrame({"household_id": household_id, "size": sizes, "hh_income": hh_income,
                               "income_band": income_band(hh_income), "head_person_id": person_id[head_idx],
                               "purchase_probability": np.round(p_buy, 6)})

    # ---------- current state per person
    cur_addr = address_history.groupby("person_id", sort=True).tail(1).set_index("person_id")
    cur_job = job_history.sort_values(["person_id", "valid_from"], kind="mergesort").groupby(
        "person_id").tail(1).set_index("person_id")
    people = pd.DataFrame({
        "person_id": person_id, "household_id": household_id[hh], "household_position": pos,
        "first_name": first, "last_name": last, "sex": sex, "dob": dob, "age_at_start": age,
        "personal_email": personal_email, "shares_household_email": share, "phone": phone,
        "hh_income": hh_income[hh], "income_band": income_band(hh_income[hh]),
    })
    people = people.join(cur_addr[["bbl", "street", "zipcode", "owner"]], on="person_id")
    people = people.join(cur_job[["employer", "title", "work_email"]], on="person_id")
    return World(people=people, households=households, address_history=address_history,
                 job_history=job_history.sort_values(["person_id", "valid_from"], kind="mergesort").reset_index(drop=True),
                 events=events)


def as_of(hist: pd.DataFrame, person_ids, dates) -> pd.DataFrame:
    """Each person's history row in force on each date (last valid_from <= date)."""
    q = pd.DataFrame({"person_id": np.asarray(person_ids, dtype=object),
                      "as_of": pd.to_datetime(np.asarray(dates, dtype="datetime64[D]")),
                      "_qid": np.arange(len(person_ids))})
    h = hist.copy()
    h["valid_from"] = pd.to_datetime(h["valid_from"])
    out = pd.merge_asof(q.sort_values("as_of", kind="mergesort"), h.sort_values("valid_from", kind="mergesort"),
                        left_on="as_of", right_on="valid_from", by="person_id", direction="backward")
    return out.sort_values("_qid", kind="mergesort").reset_index(drop=True)
