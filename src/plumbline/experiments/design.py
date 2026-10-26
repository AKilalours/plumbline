"""Design a holdout experiment for one email campaign, before anything is sent.

Who: everyone the activation audience says may be emailed at their personal address on the day the
audience was built. The unit is the inbox, not the person: families share an inbox, and randomizing
people put 510 inboxes in both arms, so "held out" people read the email their sibling was sent.
Assignment is a hash of the address with the campaign's salt, so everyone at one address shares an
arm, it is random with respect to anything about them, anyone can recompute it, and it never changes
when the list is rebuilt. Each address is sent one email.

The power calculation is written down before the send, from last month's purchase rate per inbox,
so nobody can pick the success criterion after seeing the result.
"""
from __future__ import annotations

import hashlib
import math

import duckdb
import pandas as pd

CAMPAIGN = {"id": "winter-reactivation-2026", "send_date": "2026-01-06", "window_days": 30,
            "salt": "winter-reactivation-2026", "treatment_share": 0.5,
            "primary_metric": "bought within 30 days of the send",
            "minimum_effect_worth_detecting": 0.005}    # half a point: below that, the campaign does not pay


def arm(unit: str, salt: str = CAMPAIGN["salt"], share: float = CAMPAIGN["treatment_share"]) -> str:
    h = int(hashlib.sha256(f"{salt}:{unit}".encode()).hexdigest()[:12], 16) / 16 ** 12
    return "treatment" if h < share else "holdout"


def mde(p: float, n_treat: int, n_hold: int, alpha_z: float = 1.959964, power_z: float = 0.841621) -> float:
    """Smallest true difference in conversion the test detects 80% of the time at 5% two-sided."""
    return (alpha_z + power_z) * math.sqrt(p * (1 - p) * (1 / n_treat + 1 / n_hold))


def design(con: duckdb.DuckDBPyConnection, campaign: dict = CAMPAIGN) -> dict:
    aud = con.execute("""
        SELECT a.person_key, p.personal_email AS email
        FROM products.mart_activation_audience a JOIN products.dim_person p USING (person_key)
        WHERE a.email_ok AND p.personal_email IS NOT NULL
        ORDER BY a.person_key""").df()
    aud["email"] = aud.email.str.strip().str.lower()
    aud["arm"] = [arm(e, campaign["salt"], campaign["treatment_share"]) for e in aud.email]
    con.execute("CREATE SCHEMA IF NOT EXISTS exp")
    con.execute("CREATE OR REPLACE TABLE exp.assignments AS SELECT ? AS campaign_id, person_key, email, arm FROM aud",
                [campaign["id"]])
    # last month's purchase rate in this audience: the baseline the power calculation needs
    base = con.execute("""
        SELECT avg(bought) FROM (
            SELECT a.email, max(CASE WHEN p.last_order_at > TIMESTAMP '2025-12-01' THEN 1 ELSE 0 END) AS bought
            FROM exp.assignments a JOIN products.dim_person p USING (person_key) GROUP BY a.email)""").fetchone()[0]
    inboxes = aud.drop_duplicates("email")
    nt = int((inboxes.arm == "treatment").sum())
    nh = len(inboxes) - nt
    plan = {"campaign": campaign["id"], "people": len(aud), "inboxes": len(inboxes), "treatment_inboxes": nt,
            "holdout_inboxes": nh, "unit": "inbox",
            "baseline_monthly_purchase_rate": round(float(base), 4),
            "detectable_effect_at_80pct_power": round(mde(float(base), nt, nh), 4),
            "minimum_effect_worth_detecting": campaign["minimum_effect_worth_detecting"],
            "primary_metric": campaign["primary_metric"]}
    plan["powered"] = plan["detectable_effect_at_80pct_power"] <= campaign["minimum_effect_worth_detecting"]
    con.execute("CREATE OR REPLACE TABLE exp.plan AS SELECT ? AS campaign_id, ? AS plan, now() AS written_at",
                [campaign["id"], pd.Series([plan]).to_json(orient="records")[1:-1]])
    return plan


def send_list(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """What goes to the email platform. The holdout is listed too, marked, so the platform can prove
    it sent them nothing."""
    return con.execute("SELECT DISTINCT email, arm FROM exp.assignments ORDER BY email").df()
