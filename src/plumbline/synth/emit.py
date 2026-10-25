"""Sources observe the world, imperfectly, and deliver files the way real vendors do.

Each emitter returns the delivered tables plus the answer key for them: which person
each record really is (record links) and what was damaged (damage log).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import SynthConfig
from .damage import DamageLog, abbreviate, maybe_nickname, phone_format, typo
from .world import DAY, World, as_of, d64, income_band, rand_dates


@dataclass
class Delivery:
    source: str
    path: str            # relative to the run directory
    logical_date: str
    delivered_at: str
    version: int
    frame: pd.DataFrame


@dataclass
class Emitted:
    deliveries: list[Delivery] = field(default_factory=list)
    links: list[pd.DataFrame] = field(default_factory=list)
    damage: list[DamageLog] = field(default_factory=list)


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _iso(d) -> str:
    return str(np.datetime64(d, "D"))


def _maybe_email_damage(e, rid, field_name, cfg, rng, log):
    if e is None or (isinstance(e, float) and np.isnan(e)):
        return None
    if rng.random() < cfg.missing_rate:
        log.add(rid, field_name, "missing")
        return None
    if rng.random() < cfg.typo_rate / 2:
        loc, dom = e.split("@", 1)
        log.add(rid, field_name, "typo")
        return f"{typo(loc, rng)}@{dom}"
    if rng.random() < 0.03:
        log.add(rid, field_name, "case")
        return e.upper()
    return e


# ------------------------------------------------------------------ CRM (database extract)
CRM_COLUMNS = ["crm_id", "first_name", "last_name", "email", "phone", "street_address", "zip",
               "created_at", "updated_at"]


def emit_crm(world: World, cfg: SynthConfig, rng: np.random.Generator, out: Emitted) -> np.ndarray:
    people = world.people
    start, end = d64(cfg.window_start), d64(cfg.window_end)
    n = len(people)
    m = int(round(cfg.customer_share * n))
    idx = np.sort(rng.choice(n, size=m, replace=False))
    pid = people["person_id"].to_numpy()[idx]
    created = rand_dates(rng, m, start, end - 30 * DAY)

    at_created = as_of(world.address_history, pid, created)
    current = as_of(world.address_history, pid, np.full(m, end))
    moved_after = current["valid_from"].to_numpy() > created.astype("datetime64[ns]")
    kept_current = moved_after & (rng.random(m) >= cfg.crm_stale_address_on_move)
    street = np.where(kept_current, current["street"], at_created["street"])
    zipc = np.where(kept_current, current["zipcode"], at_created["zipcode"])
    updated = np.where(kept_current, current["valid_from"].to_numpy().astype("datetime64[D]"), created)
    jobs = as_of(world.job_history, pid, created)
    has_work = jobs["work_email"].notna().to_numpy()
    use_work = has_work & (rng.random(m) < 0.15)
    email = np.where(use_work, jobs["work_email"], people["personal_email"].to_numpy()[idx])

    base = pd.DataFrame({"person_id": pid, "first_name": people["first_name"].to_numpy()[idx],
                         "last_name": people["last_name"].to_numpy()[idx], "email": email,
                         "phone": people["phone"].to_numpy()[idx], "street_address": street, "zip": zipc,
                         "created_at": created, "updated_at": updated})
    # Duplicate sign-ups: the same person registers again later, often with the other address.
    nd = int(round(cfg.crm_duplicate_rate * m))
    di = np.sort(rng.choice(m, size=nd, replace=False))
    dup = base.iloc[di].copy()
    dup["created_at"] = np.minimum(created[di] + rng.integers(1, 200, nd).astype("timedelta64[D]"), end - 30 * DAY)
    dup["updated_at"] = dup["created_at"]
    alt = np.where(use_work[di] | ~has_work[di], people["personal_email"].to_numpy()[idx[di]], jobs["work_email"].to_numpy()[di])
    dup["email"] = alt
    clean = pd.concat([base, dup], ignore_index=True).sort_values(
        ["created_at", "person_id"], kind="mergesort").reset_index(drop=True)
    clean.insert(0, "crm_id", [f"C{i:08d}" for i in range(len(clean))])

    log = DamageLog("crm")
    rows = []
    for r in clean.itertuples(index=False):
        rid = r.crm_id
        f, nick = maybe_nickname(r.first_name, cfg.nickname_rate, rng)
        if nick:
            log.add(rid, "first_name", "nickname")
        if rng.random() < cfg.typo_rate:
            f = typo(f, rng); log.add(rid, "first_name", "typo")
        l = r.last_name
        if rng.random() < cfg.typo_rate:
            l = typo(l, rng); log.add(rid, "last_name", "typo")
        e = _maybe_email_damage(r.email, rid, "email", cfg, rng, log)
        if rng.random() < cfg.missing_rate:
            ph = None; log.add(rid, "phone", "missing")
        else:
            ph = phone_format(r.phone, int(rng.integers(0, 4)))
        s = r.street_address
        if rng.random() < 0.3:
            s = abbreviate(s); log.add(rid, "street_address", "abbrev")
        if rng.random() < cfg.typo_rate / 2:
            s = typo(s, rng); log.add(rid, "street_address", "typo")
        z = r.zip
        if rng.random() < cfg.missing_rate / 2:
            z = None; log.add(rid, "zip", "missing")
        rows.append((rid, f, l, e, ph, s.title(), z, _iso(r.created_at), _iso(r.updated_at)))
    damaged = pd.DataFrame(rows, columns=CRM_COLUMNS)

    full_day, ch1, ch2 = end - 2 * DAY, end - DAY, end
    out.deliveries.append(Delivery("crm", f"sources/crm/crm_full_{_iso(full_day)}.csv", _iso(full_day),
                                   f"{_iso(ch1)}T01:00:00", 1, damaged))
    # Change files: corrections of earlier typos, as a CRM would emit after a customer fixes their profile.
    dmg = pd.DataFrame(log.rows, columns=["source", "record_id", "field", "kind"])
    fixable = dmg[(dmg["kind"] == "typo") & dmg["field"].isin(["last_name", "email"])]["record_id"].unique()
    fixable = np.sort(fixable)
    per_day = max(1, int(round(0.005 * len(damaged))))
    pick = rng.choice(len(fixable), size=min(len(fixable), 2 * per_day), replace=False) if len(fixable) else []
    chosen = fixable[np.sort(pick)] if len(fixable) else np.array([], dtype=object)
    by_id = damaged.set_index("crm_id")
    clean_by_id = clean.set_index("crm_id")
    for day, ids in ((ch1, chosen[:per_day]), (ch2, chosen[per_day:])):
        fixed = by_id.loc[ids].copy()
        for rid in ids:
            fields = dmg[(dmg["record_id"] == rid) & (dmg["kind"] == "typo")]["field"]
            for fld in fields:
                if fld in ("last_name", "email"):
                    fixed.at[rid, fld] = clean_by_id.at[rid, fld]
        fixed["updated_at"] = _iso(day)
        out.deliveries.append(Delivery("crm", f"sources/crm/crm_changes_{_iso(day)}.csv", _iso(day),
                                       f"{_iso(day + DAY)}T01:00:00", 1, fixed.reset_index()[CRM_COLUMNS]))
    out.links.append(pd.DataFrame({"source": "crm", "record_id": clean["crm_id"], "person_id": clean["person_id"]}))
    out.damage.append(log)
    return np.unique(clean["person_id"].to_numpy())


# ------------------------------------------------------------------ vendor (weekly third-party file)
VENDOR_COLUMNS = ["vendor_record_id", "full_name", "dob", "address_line", "zip", "phone", "email",
                  "employer", "job_title", "est_income_band", "homeowner_flag"]


def _fmt_name(first: str, last: str, style: int) -> str:
    return f"{last.upper()}, {first.upper()}" if style == 0 else f"{first} {last}"


def _fmt_dob(d, style: float) -> str | None:
    s = str(np.datetime64(d, "D"))
    if style < 0.60:
        return s
    if style < 0.85:
        return f"{s[5:7]}/{s[8:10]}/{s[0:4]}"
    if style < 0.95:
        return s[0:4]
    return None


def emit_vendor(world: World, cfg: SynthConfig, rng: np.random.Generator, out: Emitted) -> np.ndarray:
    people = world.people
    start, end = d64(cfg.window_start), d64(cfg.window_end)
    n = len(people)
    m = int(round(cfg.vendor_coverage * n))
    idx = np.sort(rng.choice(n, size=m, replace=False))
    pid = people["person_id"].to_numpy()[idx]
    week = rng.integers(1, cfg.vendor_weeks + 1, size=m)
    week_date = end - ((cfg.vendor_weeks - week) * 7).astype("timedelta64[D]")

    now = as_of(world.address_history, pid, week_date)
    orig = as_of(world.address_history, pid, np.full(m, start))
    moved_before = now["valid_from"].to_numpy() > np.datetime64(start, "ns")
    stale = moved_before & (rng.random(m) < cfg.vendor_stale_address_on_move)
    street = np.where(stale, orig["street"], now["street"])
    zipc = np.where(stale, orig["zipcode"], now["zipcode"])
    owner_true = now["owner"].to_numpy().astype(bool)
    jobs = as_of(world.job_history, pid, week_date)
    has_work = jobs["work_email"].notna().to_numpy()
    use_work = has_work & (rng.random(m) < 0.30)
    email = np.where(use_work, jobs["work_email"], people["personal_email"].to_numpy()[idx])
    band_true = people["income_band"].to_numpy()[idx]
    u = rng.random(m)
    band = np.where(u < 0.65, band_true,
                    np.where(u < 0.90, np.clip(band_true + rng.choice([-1, 1], m), 1, 6), rng.integers(1, 7, m)))
    flag_ok = rng.random(m) < 0.85
    flag = np.where(flag_ok, owner_true, ~owner_true)
    flag_s = np.where(rng.random(m) < cfg.missing_rate, "", np.where(flag, "Y", "N"))
    confidence = np.round(rng.uniform(0.5, 1.0, m), 2)
    vid = [f"V{hashlib.blake2b(f'{cfg.seed}:{i}'.encode(), digest_size=8).hexdigest()}" for i in idx]

    log = DamageLog("vendor")
    clean_rows, rows = [], []
    first = people["first_name"].to_numpy()[idx]
    last = people["last_name"].to_numpy()[idx]
    dob = people["dob"].to_numpy()[idx]
    phone = people["phone"].to_numpy()[idx]
    for j in range(m):
        rid = vid[j]
        style = int(rng.integers(0, 2))
        f, nick = maybe_nickname(first[j], cfg.nickname_rate, rng)
        if nick:
            log.add(rid, "full_name", "nickname")
        l = last[j]
        if rng.random() < cfg.typo_rate:
            l = typo(l, rng); log.add(rid, "full_name", "typo")
        a = street[j]
        if rng.random() < 0.3:
            a = abbreviate(a); log.add(rid, "address_line", "abbrev")
        if rng.random() < cfg.typo_rate / 2:
            a = typo(a, rng); log.add(rid, "address_line", "typo")
        e = _maybe_email_damage(email[j], rid, "email", cfg, rng, log)
        ph = None if rng.random() < cfg.missing_rate else phone_format(phone[j], int(rng.integers(0, 4)))
        if ph is None:
            log.add(rid, "phone", "missing")
        dob_s = _fmt_dob(dob[j], rng.random())
        common = dict(dob=dob_s, zip=zipc[j], employer=jobs["employer"].iat[j], job_title=jobs["title"].iat[j],
                      est_income_band=int(band[j]), homeowner_flag=flag_s[j], source_confidence=confidence[j],
                      week=int(week[j]), person_id=pid[j])
        clean_rows.append(dict(vendor_record_id=rid, full_name=_fmt_name(first[j], last[j], style),
                               address_line=street[j], phone=phone_format(phone[j], 0), email=email[j], **common))
        rows.append(dict(vendor_record_id=rid, full_name=_fmt_name(f, l, style), address_line=a,
                         phone=ph, email=e, **common))
    damaged = pd.DataFrame(rows)
    clean = pd.DataFrame(clean_rows)
    dmg = pd.DataFrame(log.rows, columns=["source", "record_id", "field", "kind"])

    def shape(frame: pd.DataFrame, w: int) -> pd.DataFrame:
        cols = list(VENDOR_COLUMNS)
        f = frame.copy()
        if w >= cfg.vendor_rename_week:
            f = f.rename(columns={"job_title": "occupation"})
            cols = [("occupation" if c == "job_title" else c) for c in cols] + ["source_confidence"]
        return f[cols]

    delivered_before = []
    for w in range(1, cfg.vendor_weeks + 1):
        wd = end - (cfg.vendor_weeks - w) * 7 * DAY
        this = damaged[damaged["week"] == w]
        if delivered_before:
            prev = pd.concat(delivered_before, ignore_index=True)
            nrep = min(len(prev), int(round(cfg.vendor_repeat_rate * len(this))))
            rep = prev.iloc[np.sort(rng.choice(len(prev), size=nrep, replace=False))] if nrep else prev.iloc[:0]
            body = pd.concat([this, rep], ignore_index=True)
        else:
            body = this
        delivered_before.append(this)
        path = f"sources/vendor/vendor_week_{w:02d}_{_iso(wd)}.csv"
        out.deliveries.append(Delivery("vendor", path, _iso(wd), f"{_iso(wd + DAY)}T06:00:00", 1, shape(body, w)))
        if w == cfg.vendor_resent_week:
            # Re-sent two days later with name typos corrected in about 2% of rows.
            typo_ids = set(dmg[(dmg["field"] == "full_name") & (dmg["kind"] == "typo")]["record_id"])
            cand = np.flatnonzero(body["vendor_record_id"].isin(typo_ids).to_numpy())
            nfix = min(len(cand), max(1, int(round(0.02 * len(body)))))
            fix = cand[np.sort(rng.choice(len(cand), size=nfix, replace=False))] if len(cand) else []
            v2 = body.copy()
            cl = clean.set_index("vendor_record_id")
            for i in fix:
                v2.iat[i, v2.columns.get_loc("full_name")] = cl.at[v2["vendor_record_id"].iat[i], "full_name"]
            out.deliveries.append(Delivery("vendor", path.replace(".csv", "_v2.csv"), _iso(wd),
                                           f"{_iso(wd + 3 * DAY)}T06:00:00", 2, shape(v2, w)))
    out.links.append(pd.DataFrame({"source": "vendor", "record_id": vid, "person_id": pid}))
    out.damage.append(log)
    return np.unique(pid)


# ------------------------------------------------------------------ email engagement (daily files)
EMAIL_COLUMNS = ["event_id", "email", "event_type", "campaign_id", "event_ts"]


def emit_email(world: World, cfg: SynthConfig, rng: np.random.Generator, out: Emitted, customers: np.ndarray) -> None:
    end = d64(cfg.window_end)
    people = world.people.set_index("person_id")
    em = people.loc[customers, "personal_email"].to_numpy()
    links = []
    for di in range(cfg.email_days):
        day = end - (cfg.email_days - 1 - di) * DAY
        camp_day = day - (di % 2) * DAY
        campaign = f"CMP-{_iso(camp_day).replace('-', '')}"
        sent = np.flatnonzero(rng.random(len(em)) < 0.20)
        t_send = rng.integers(8 * 3600, 20 * 3600, len(sent))
        opened = rng.random(len(sent)) < 0.25
        t_open = t_send + rng.integers(60, 6 * 3600, len(sent))
        clicked = opened & (rng.random(len(sent)) < 0.20)
        t_click = t_open + rng.integers(5, 600, len(sent))
        unsub = rng.random(len(sent)) < 0.002
        t_unsub = t_send + rng.integers(60, 3600, len(sent))
        parts = [(sent, t_send, "send"), (sent[opened], t_open[opened], "open"),
                 (sent[clicked], t_click[clicked], "click"), (sent[unsub], t_unsub[unsub], "unsubscribe")]
        recs = pd.concat([pd.DataFrame({"i": i, "sec": s, "event_type": t}) for i, s, t in parts], ignore_index=True)
        recs = recs.sort_values(["sec", "i", "event_type"], kind="mergesort").reset_index(drop=True)
        ts = pd.Timestamp(str(day)) + pd.to_timedelta(recs["sec"], unit="s")
        addr = em[recs["i"].to_numpy()]
        upper = rng.random(len(recs)) < 0.05
        addr = np.where(upper, np.char.upper(addr.astype(str)), addr)
        frame = pd.DataFrame({"event_id": [f"EM{_iso(day).replace('-', '')}{j:06d}" for j in range(len(recs))],
                              "email": addr, "event_type": recs["event_type"], "campaign_id": campaign,
                              "event_ts": ts.dt.strftime("%Y-%m-%dT%H:%M:%S")})
        links.append(pd.DataFrame({"source": "email", "record_id": frame["event_id"],
                                   "person_id": customers[recs["i"].to_numpy()]}))
        late = rng.random() < cfg.email_late_rate
        lag = int(rng.integers(2, 5)) if late else 1
        delivered = f"{_iso(day + lag * DAY)}T{'06' if late else '01'}:00:00"
        base = f"sources/email/email_events_{_iso(day)}"
        if di + 1 == cfg.email_corrected_day and len(frame) > 20:
            hold = np.sort(rng.choice(len(frame), size=10, replace=False))
            kept = frame.drop(index=hold)
            ndup = max(1, int(round(0.02 * len(kept))))
            dups = kept.iloc[np.sort(rng.choice(len(kept), size=ndup, replace=False))]
            v1 = pd.concat([kept, dups], ignore_index=True)
            out.deliveries.append(Delivery("email", f"{base}_v1.csv", _iso(day), delivered, 1, v1))
            out.deliveries.append(Delivery("email", f"{base}_v2.csv", _iso(day), f"{_iso(day + (lag + 1) * DAY)}T06:00:00", 2,
                                           kept.reset_index(drop=True)))
            out.deliveries.append(Delivery("email", f"{base}_v3.csv", _iso(day), f"{_iso(day + (lag + 2) * DAY)}T06:00:00", 3,
                                           frame))
        else:
            out.deliveries.append(Delivery("email", f"{base}_v1.csv", _iso(day), delivered, 1, frame))
    out.links.append(pd.concat(links, ignore_index=True))


# ------------------------------------------------------------------ privacy (deletion requests)
def emit_deletions(world: World, cfg: SynthConfig, rng: np.random.Generator, out: Emitted,
                   observed: np.ndarray) -> pd.DataFrame:
    end = d64(cfg.window_end)
    people = world.people.set_index("person_id")
    cnt = max(1, int(round(cfg.deletion_request_rate * len(world.people))))
    chosen = np.sort(rng.choice(observed, size=min(cnt, len(observed)), replace=False))
    received = rand_dates(rng, len(chosen), end - 30 * DAY, end)
    emails = people.loc[chosen, "personal_email"].to_numpy()
    phones = people.loc[chosen, "phone"].to_numpy()
    frame = pd.DataFrame({
        "request_id": [f"R{i:07d}" for i in range(len(chosen))],
        "email_sha256": [_sha(str(e).strip().lower()) for e in emails],
        "phone_sha256": [_sha(str(p)) for p in phones],
        "received_date": [_iso(d) for d in received],
    })
    out.deliveries.append(Delivery("privacy", f"sources/privacy/deletion_requests_{_iso(end)}.csv", _iso(end),
                                   f"{_iso(end)}T23:00:00", 1, frame))
    return pd.DataFrame({"request_id": frame["request_id"], "person_id": chosen})
