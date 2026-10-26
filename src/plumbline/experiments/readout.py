"""Read out a holdout experiment: did the email cause purchases, for whom, and whom to send it to.

1. Attribute the store's orders in the 30 days after the send to people, through every address the
   identity graph knows for them (people check out with their work address, or mistype one).
2. Check the experiment before believing it: the treatment/holdout split must match the design
   (a sample-ratio mismatch means assignment or logging broke, and no lift number survives that).
3. The effect: difference in purchase rate with a 95% interval, then the same with CUPED (subtract
   what each person's order history already predicts), which narrows the interval without bias
   because the history was fixed before the send.
4. By segment, with Holm-adjusted p-values: three looks at the data are three chances of a fluke.
5. What the numbers miss: orders nobody can attribute (a mistyped address) count as "did not buy"
   in both arms, which shrinks the measured effect toward zero by the attributed share. The readout
   reports the raw effect and one divided by that share, and says the second rests on an assumption
   (the loss is the same in both arms). Bounced addresses are people the email never reached; the
   effect of sending to the list includes them, as it should, because the list is what was decided.
6. Uplift model: who does the email change, not who buys. Two models (treated, held out), uplift is
   the difference; chosen on one half of the people, judged on the other half by the Qini curve.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy import stats
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from ..ml.train import SeedEnsemble
from .design import CAMPAIGN

SRM_ALPHA = 0.001          # a split this unlikely means the pipeline, not chance
MIN_ATTRIBUTED = 0.85      # below this, too many buyers are invisible for the rates to mean anything
TARGET_SHARE = 0.30        # next send: the top 30% by each policy
FEATURES = ["orders", "revenue", "days_since_last_order", "is_customer", "lapsed", "high_value", "income_band",
            "orders_window", "revenue_window", "opens_90d", "clicks_90d", "log_zip_income", "moves_window",
            "new_job_window", "work_emails_seen"]


def _half(key: str, salt: str) -> int:
    return int(hashlib.sha256(f"{salt}:{key}".encode()).hexdigest()[:8], 16) % 2


def load_orders(con, run_dir: Path) -> int:
    # Explicit types: an empty or all-blank column would otherwise be typed by whatever pandas
    # infers (an untyped column on pandas 2, which DuckDB reads as INTEGER), and email comparisons fail.
    o = pd.read_csv(Path(run_dir) / "campaign" / "orders.csv", dtype=str, keep_default_na=False)
    o["customer_email"] = o.customer_email.str.strip().str.lower()
    o = o.drop_duplicates("order_id")
    con.execute("""CREATE OR REPLACE TABLE exp.campaign_orders AS
                   SELECT CAST(order_id AS VARCHAR) AS order_id, CAST(customer_email AS VARCHAR) AS customer_email,
                          CAST(created_at AS TIMESTAMP) AS created_ts,
                          CAST(nullif(total_price, '') AS DOUBLE) AS total_price FROM o""")
    return len(o)


def outcomes(con, campaign: dict = CAMPAIGN) -> tuple[pd.DataFrame, dict]:
    """One row per inbox (the unit that was randomized): arm, bought, revenue, pre-send covariates.

    An order belongs to an inbox when it was placed with that address, or with any other address the
    identity graph ties to one of the inbox's people (a work address, say)."""
    start = pd.Timestamp(campaign["send_date"])
    end = start + pd.Timedelta(days=campaign["window_days"])
    con.execute("""
        CREATE OR REPLACE TEMP TABLE email_owner AS
        WITH known AS (
            SELECT lower(personal_email) AS email, person_key FROM products.dim_person WHERE personal_email IS NOT NULL
            UNION SELECT lower(work_email), person_key FROM products.dim_person WHERE work_email IS NOT NULL
            UNION SELECT lower(email), person_key FROM identity_records WHERE email IS NOT NULL AND person_key IS NOT NULL)
        SELECT email, any_value(person_key) AS person_key FROM known GROUP BY email
        HAVING count(DISTINCT person_key) = 1          -- an address several people use names nobody""")
    att = con.execute("""
        SELECT o.order_id, o.total_price,
               coalesce(direct.email, via.email) AS inbox
        FROM exp.campaign_orders o
        LEFT JOIN (SELECT DISTINCT email FROM exp.assignments) direct ON direct.email = o.customer_email
        LEFT JOIN email_owner e ON e.email = o.customer_email
        LEFT JOIN exp.assignments via ON via.person_key = e.person_key
        WHERE o.created_ts >= ? AND o.created_ts < ?""", [start, end]).df()
    found = con.execute("""
        SELECT count(*) FROM exp.campaign_orders o
        WHERE o.created_ts >= ? AND o.created_ts < ?
          AND (o.customer_email IN (SELECT email FROM email_owner) OR o.customer_email IN (SELECT email FROM exp.assignments))""",
                        [start, end]).fetchone()[0]
    att = att.drop_duplicates("order_id")
    att_info = {"orders_in_window": len(att), "attributed_to_an_inbox": int(att.inbox.notna().sum()),
                "attributed_share": round(float(att.inbox.notna().mean()), 4) if len(att) else None,
                "orders_from_people_outside_the_experiment": int(found - att.inbox.notna().sum())}
    per = att.dropna(subset=["inbox"]).groupby("inbox").agg(orders_after=("order_id", "size"), revenue_after=("total_price", "sum"))
    people = con.execute("""
        SELECT a.email, a.arm, a.person_key, p.orders, p.revenue,
               date_diff('day', p.last_order_at, TIMESTAMP '2025-12-31') AS days_since_last_order,
               m.is_customer, m.lapsed, m.high_value, m.income_band,
               f.orders_window, f.revenue_window, f.opens_90d, f.clicks_90d, f.log_zip_income,
               f.moves_window, f.new_job_window, f.work_emails_seen
        FROM exp.assignments a
        JOIN products.dim_person p USING (person_key)
        JOIN products.mart_activation_audience m USING (person_key)
        LEFT JOIN ml.feature_snapshots f ON f.person_key = a.person_key AND f.as_of = DATE '2025-12-31'""").df()
    for c in ("is_customer", "lapsed", "high_value"):
        people[c] = people[c].astype(float)
    people["recent"] = people.is_customer * (1 - people.lapsed)
    agg = {c: "max" for c in FEATURES if c not in ("orders", "revenue", "days_since_last_order")}
    agg.update({"orders": "sum", "revenue": "sum", "days_since_last_order": "min", "arm": "first",
                "person_key": "size", "recent": "max"})
    df = people.groupby("email", sort=True).agg(agg).rename(columns={"person_key": "people"}).join(per)
    df = df.reset_index()
    df["bought"] = (df.orders_after.fillna(0) > 0).astype(int)
    df["revenue_after"] = df.revenue_after.fillna(0.0)
    df["segment"] = np.where(df.recent > 0, "recent customer", np.where(df.lapsed > 0, "lapsed", "never ordered"))
    return df, att_info


def true_inbox_effect(run_dir: Path, inboxes: pd.Series) -> pd.Series:
    """From the answer key: how much sending to each inbox raises the chance anyone there buys.
    An inbox that reaches nobody has an effect of zero."""
    owners = pd.read_parquet(run_dir / "truth" / "campaign_inboxes.parquet")
    t = pd.read_parquet(run_dir / "truth" / "campaign_truth.parquet", columns=["person_id", "p0", "tau"])
    o = owners.merge(t, on="person_id")
    o["log_none_without"] = np.log1p(-np.clip(o.p0, 0, 1 - 1e-12))
    o["log_none_with"] = np.log1p(-np.clip(o.p0 + o.tau, 0, 1 - 1e-12))
    g = o.groupby("email")[["log_none_without", "log_none_with"]].sum()
    eff = np.exp(g.log_none_without) - np.exp(g.log_none_with)
    return inboxes.map(eff).fillna(0.0)


def diff(y: np.ndarray, t: np.ndarray) -> dict:
    yt, yc = y[t == 1], y[t == 0]
    d = yt.mean() - yc.mean()
    se = math.sqrt(yt.var(ddof=1) / len(yt) + yc.var(ddof=1) / len(yc))
    p = 2 * (1 - stats.norm.cdf(abs(d) / se)) if se > 0 else float("nan")
    return {"treatment": round(float(yt.mean()), 5), "holdout": round(float(yc.mean()), 5), "effect": round(float(d), 5),
            "ci95": [round(float(d - 1.96 * se), 5), round(float(d + 1.96 * se), 5)], "se": float(se), "p": float(p)}


def cuped(y: np.ndarray, x: np.ndarray, t: np.ndarray) -> dict:
    """Y minus theta x (X - mean X): same expected effect, smaller variance when X predicts Y."""
    x = np.nan_to_num(x.astype(float))
    theta = np.cov(y, x)[0, 1] / x.var(ddof=1) if x.var() > 0 else 0.0
    r = diff(y - theta * (x - x.mean()), t)
    return {**r, "theta": round(float(theta), 6)}


def holm(ps: list[float]) -> list[float]:
    order = np.argsort(ps)
    m, out, running = len(ps), [0.0] * len(ps), 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * ps[i]))
        out[i] = running
    return out


def qini_auc(uplift: np.ndarray, y: np.ndarray, t: np.ndarray) -> float:
    """Area between the Qini curve and the random-targeting line, per person, so it is comparable
    across sets of different sizes. Positive means the ranking puts responders first."""
    o = np.argsort(-uplift, kind="mergesort")
    y, t = y[o], t[o]
    ct, cc = np.cumsum(y * t), np.cumsum(y * (1 - t))
    nt, nc = np.cumsum(t), np.cumsum(1 - t)
    q = ct - cc * np.divide(nt, nc, out=np.zeros(len(nc), dtype=float), where=nc > 0)
    rand = q[-1] * np.arange(1, len(q) + 1) / len(q)
    return float((q - rand).mean() / len(q))


def _learners() -> dict:
    return {"logistic": lambda: make_pipeline(SimpleImputer(strategy="median", add_indicator=True), StandardScaler(),
                                              LogisticRegression(C=1.0, max_iter=3000)),
            "gradient_boosting": lambda: SeedEnsemble(max_iter=200, learning_rate=0.05, max_leaf_nodes=15,
                                                      l2_regularization=1.0, min_samples_leaf=100)}


class TLearner:
    def __init__(self, make):
        self.t, self.c = make(), make()

    def fit(self, X, y, t):
        self.t.fit(X[t == 1], y[t == 1])
        self.c.fit(X[t == 0], y[t == 0])
        return self

    def baseline(self, X):
        return self.c.predict_proba(X)[:, 1]

    def uplift(self, X):
        return self.t.predict_proba(X)[:, 1] - self.c.predict_proba(X)[:, 1]


def _X(df: pd.DataFrame) -> pd.DataFrame:
    return df[FEATURES].astype("float64")


def uplift_model(df: pd.DataFrame) -> dict:
    fit_half = df.email.map(lambda k: _half(k, "uplift-split")) == 0
    inner = df.email.map(lambda k: _half(k, "uplift-inner")) == 0
    tr, va, ev = df[fit_half & inner], df[fit_half & ~inner], df[~fit_half]
    choice = {}
    for name, make in _learners().items():
        m = TLearner(make).fit(_X(tr), tr.bought.to_numpy(), tr.t.to_numpy())
        choice[name] = qini_auc(m.uplift(_X(va)), va.bought.to_numpy(), va.t.to_numpy())
    best = max(choice, key=choice.get)
    fit = df[fit_half]
    model = TLearner(_learners()[best]).fit(_X(fit), fit.bought.to_numpy(), fit.t.to_numpy())
    up, base = model.uplift(_X(ev)), model.baseline(_X(ev))
    return {"chosen": best, "validation_qini": {k: round(v, 6) for k, v in choice.items()},
            "test_qini": round(qini_auc(up, ev.bought.to_numpy(), ev.t.to_numpy()), 6),
            "_eval": ev.assign(pred_uplift=up, pred_baseline=base)}


def policies(ev: pd.DataFrame, share: float = TARGET_SHARE) -> dict:
    """Incremental buyers per 1,000 emails if next time only the top `share` get it, using the true
    effect from the answer key. Only an evaluation can do this; a real brand sees the Qini curve."""
    k = int(round(share * len(ev)))
    tau = ev.true_tau.to_numpy()

    def top(score):
        return round(float(tau[np.argsort(-score, kind="mergesort")[:k]].mean() * 1000), 2)
    rng = np.random.default_rng(0)
    return {"everyone": round(float(tau.mean() * 1000), 2),
            "random_30pct": round(float(rng.permutation(tau)[:k].mean() * 1000), 2),
            "likeliest_buyers_30pct": top(ev.pred_baseline.to_numpy()),
            "uplift_model_30pct": top(ev.pred_uplift.to_numpy()),
            "best_possible_30pct": top(tau)}


def readout(con, run_dir: str | Path, campaign: dict = CAMPAIGN) -> dict:
    run_dir = Path(run_dir)
    load_orders(con, run_dir)
    df, att = outcomes(con, campaign)
    df["t"] = (df.arm == "treatment").astype(int)
    n, nt = len(df), int(df.t.sum())
    exp_t = n * campaign["treatment_share"]
    srm_p = float(stats.chisquare([nt, n - nt], [exp_t, n - exp_t]).pvalue)
    y, t = df.bought.to_numpy(), df.t.to_numpy()
    primary = diff(y, t)
    adj = cuped(y, df.orders.to_numpy(), t)
    rev = diff(df.revenue_after.to_numpy(), t)
    segs = []
    for name, g in df.groupby("segment"):
        r = diff(g.bought.to_numpy(), g.t.to_numpy())
        segs.append({"segment": name, "inboxes": len(g), **{k: v for k, v in r.items() if k != "se"}})
    for s, p in zip(segs, holm([s["p"] for s in segs])):
        s["p_holm"] = round(p, 6)
        s["p"] = round(s["p"], 6)

    share = att["attributed_share"] or 1.0
    corrected = {"effect": round(primary["effect"] / share, 5),
                 "ci95": [round(primary["ci95"][0] / share, 5), round(primary["ci95"][1] / share, 5)],
                 "assumes": "unattributed orders are lost at the same rate in both arms"}
    bounces = pd.read_csv(run_dir / "campaign" / "bounces.csv") if (run_dir / "campaign" / "bounces.csv").exists() else pd.DataFrame(columns=["email"])
    bounce_rate = round(len(bounces) / nt, 4) if nt else None

    df["true_tau"] = true_inbox_effect(run_dir, df.email).to_numpy()
    true_ate = float(df.true_tau.mean())
    reached = df.email.isin(pd.read_parquet(run_dir / "truth" / "campaign_inboxes.parquet").email)
    true_reached = float(df.true_tau[reached].mean())
    both = int(pd.read_parquet(run_dir / "truth" / "campaign_truth.parquet", columns=["in_both_arms"]).in_both_arms.sum())
    up = uplift_model(df)
    ev = up.pop("_eval")
    seg_truth = df.groupby("segment").true_tau.mean()
    for s_ in segs:
        s_["true_effect"] = round(float(seg_truth.get(s_["segment"], float("nan"))), 5)
        s_["truth_inside_ci"] = bool(s_["ci95"][0] <= s_["true_effect"] <= s_["ci95"][1])

    out = {
        "campaign": campaign["id"], "unit": "inbox", "inboxes": n, "treatment": nt, "holdout": n - nt,
        "people": int(df.people.sum()),
        "sample_ratio": {"p": round(srm_p, 4), "ok": srm_p >= SRM_ALPHA},
        "attribution": att,
        "bought_within_30_days": {k: v for k, v in primary.items() if k != "se"},
        "with_cuped": {k: v for k, v in adj.items() if k != "se"},
        "corrected_for_unattributed_orders": corrected,
        "bounce_rate_in_treatment": bounce_rate,
        "cuped_interval_narrower_by": round(1 - adj["se"] / primary["se"], 4) if primary["se"] else None,
        "revenue_per_person": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in rev.items() if k != "se"},
        "segments": segs,
        "uplift": {**up, "spearman_with_true_effect": round(float(stats.spearmanr(ev.pred_uplift, ev.true_tau).statistic), 4),
                   "incremental_buyers_per_1000_emails": policies(ev)},
        "answer_key": {"true_effect_of_sending_to_this_list": round(true_ate, 5),
                       "true_effect_on_inboxes_that_reach_someone": round(true_reached, 5),
                       "addresses_that_reach_nobody": round(float(1 - reached.mean()), 4),
                       "people_in_both_arms_through_two_records": both,
                       "inside_raw_ci": bool(primary["ci95"][0] <= true_ate <= primary["ci95"][1]),
                       "inside_corrected_ci": bool(corrected["ci95"][0] <= true_ate <= corrected["ci95"][1])},
    }
    out["trustworthy"] = bool(out["sample_ratio"]["ok"] and (att["attributed_share"] or 0) >= MIN_ATTRIBUTED)
    con.execute("CREATE OR REPLACE TABLE exp.readouts AS SELECT ? AS campaign_id, ? AS readout, now() AS read_at",
                [campaign["id"], pd.Series([out]).to_json(orient="records", default_handler=str)[1:-1]])
    return out
