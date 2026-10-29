"""Plumbline, live: a customer-data reliability console on a synthetic city whose truth is known.

Run it with the project's Python:  python -m streamlit run streamlit_app.py
The page only draws. Every operation is in plumbline.app.ops and plumbline.app.queries (tested
without Streamlit); saved scale results come from docs/results/scale.json.
"""
from __future__ import annotations

import html
import json
import os
import random
import sys
import tempfile
import threading
import time
import uuid
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
# A hosted app gets about 2.7 GB. Uncapped, Splink's DuckDB alone took 2.2 GB for a city this size.
os.environ.setdefault("PLUMBLINE_DUCKDB_MEMORY", "1GB")

import streamlit as st  # noqa: E402

from plumbline.app import preflight  # noqa: E402  (standard library only)

st.set_page_config(page_title="Plumbline", page_icon="📏", layout="wide")

# Visual language after Minerva's site: white pages, royal blue, deep navy, a gold accent, pill controls,
# small monospace labels. Their style, not their name or logo: this is an independent project.
BLUE, NAVY, GOLD, INK, MUTED, LINE, TINT = "#2941B9", "#11196F", "#E5B74D", "#050505", "#6B6B6B", "#E6E6E6", "#F3F5FC"
GREEN, RED = "#1F8A5B", "#C2412D"
st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500&display=swap');
html, body, [class*="css"], .stMarkdown, p, li, label, input, textarea, button, [data-testid="stMetricValue"] {{
  font-family: 'Geist', system-ui, sans-serif; }}
[data-testid="stAppViewContainer"], [data-testid="stHeader"] {{ background: #FFFFFF; }}
[data-testid="stSidebar"] {{ background: #FFFFFF; border-right: 1px solid {LINE}; }}
h1 {{ font-family: 'Geist', sans-serif !important; font-weight: 500 !important; color: {BLUE} !important;
     font-size: 2.6rem !important; letter-spacing: -0.02em; line-height: 1.08 !important; }}
h2, h3 {{ font-family: 'Geist', sans-serif !important; font-weight: 500 !important; color: {INK}; letter-spacing: -0.01em; }}
.block-container {{ max-width: 1200px; padding-top: 2rem; }}
.lede {{ font-size: 1.12rem; color: {INK}; margin: -0.3rem 0 1.4rem 0; padding-bottom: 1.1rem; border-bottom: 1px solid {LINE}; }}
.mono {{ font-family: 'Geist Mono', monospace; text-transform: uppercase; letter-spacing: 0.06em; font-size: 0.72rem; color: {MUTED}; }}
.big {{ font-size: 2.1rem; font-weight: 500; color: {INK}; line-height: 1.1; font-variant-numeric: tabular-nums; }}
.big small {{ font-size: 0.95rem; color: {MUTED}; font-weight: 400; }}
.dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; vertical-align: middle; }}
.pass {{ background: {GREEN}; }} .warn {{ background: {GOLD}; }} .fail {{ background: {RED}; }}
.note {{ color: {MUTED}; font-size: 0.86rem; margin-top: 0.3rem; }}
[class*="st-key-card_"] {{ border: 1px solid {LINE} !important; border-radius: 16px !important; padding: 1.1rem 1.2rem !important;
                          background: #FFFFFF; transition: border-color .15s; }}
[class*="st-key-card_"]:hover {{ border-color: {BLUE} !important; }}
[class*="st-key-band"] {{ background: linear-gradient(120deg, {NAVY} 0%, {BLUE} 70%, #4A63D8 100%); border-radius: 18px;
                          padding: 1.4rem 1.6rem !important; }}
[class*="st-key-band"] [data-testid="stWidgetLabel"] p, [class*="st-key-band"] .mono, [class*="st-key-band"] .big,
[class*="st-key-band"] .big small {{ color: #FFFFFF !important; }}
[class*="st-key-band"] button p {{ color: {INK} !important; }}
.stButton button, .stDownloadButton button, [data-testid="stPopover"] button, [data-testid="stPageLink"] a {{ border-radius: 9999px !important; }}
.stButton button[kind="primary"] {{ background: {INK}; border-color: {INK}; }}
.stButton button[kind="primary"]:hover {{ background: {BLUE}; border-color: {BLUE}; }}
[data-testid="stMetricValue"] {{ font-weight: 500; }}
[data-testid="stMetricLabel"] p {{ font-family: 'Geist Mono', monospace; text-transform: uppercase; letter-spacing: 0.06em;
                                   font-size: 0.72rem; color: {MUTED}; }}
.cards {{ display: flex; flex-wrap: wrap; gap: 10px; margin: 0.4rem 0 1rem 0; }}
.card {{ border: 1px solid {LINE}; border-radius: 12px; padding: 8px 12px; background: white; min-width: 150px;
        font-size: 0.86rem; line-height: 1.35; }}
.card b {{ display: block; font-family: 'Geist Mono', monospace; font-size: 0.68rem; text-transform: uppercase; color: {MUTED}; font-weight: 500; }}
.card.gone {{ background: #F1F8F4; border-color: {GREEN}; color: {MUTED}; text-decoration: line-through; }}
.card.left, .card.innocent {{ border-color: {RED}; background: #FBEDEA; }}
.verdict {{ font-size: 1.1rem; font-weight: 500; margin: 0.3rem 0 0.8rem 0; }}
.ok {{ color: {GREEN}; }} .bad {{ color: {RED}; }}
.chip {{ display: inline-block; border: 1px solid {INK}; border-radius: 9999px; padding: 3px 12px; margin: 0 6px 6px 0;
        font-family: 'Geist Mono', monospace; font-size: 0.74rem; text-transform: uppercase; }}
.chip.blue {{ border-color: {BLUE}; color: {BLUE}; }}
.step {{ border-left: 2px solid {BLUE}; padding: 2px 0 2px 12px; margin: 6px 0; }}
</style>
""", unsafe_allow_html=True)

_missing = preflight.missing()
if _missing:
    st.title("Plumbline cannot start on this Python")
    _what, _fix, _why = preflight.advice(_missing)
    st.error(_what)
    st.code(_fix, language="bash")
    st.caption(_why)
    st.stop()

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402

from plumbline.app import ops as O  # noqa: E402
from plumbline.app import queries as Q  # noqa: E402
from plumbline.app.demo import STAGES, build  # noqa: E402

N_PEOPLE = int(os.environ.get("PLUMBLINE_DEMO_PEOPLE", "4000"))
SEED = 11
ROOT = Path(os.environ.get("PLUMBLINE_DEMO_DIR", Path(tempfile.gettempdir()) / "plumbline-demo"))
USE_MODEL = os.environ.get("PLUMBLINE_APP_USE_LLM") == "1"
REPO_URL = "https://github.com/AKilalours/plumbline"
SIZES = {"20k": 20_000, "100k": 100_000, "300k": 300_000, "1M": 1_000_000}
LOG = O.ActivityLog(ROOT.parent / f"{ROOT.name}-activity.jsonl")


# ----------------------------------------------------------------- the world
@st.cache_resource(show_spinner=False)
def _build_lock() -> threading.Lock:
    return threading.Lock()


def _already_built() -> bool:
    try:
        s = json.loads((ROOT / "summary.json").read_text())
        return (s.get("n_people"), s.get("seed")) == (N_PEOPLE, SEED) and (ROOT / "warehouse.duckdb").exists()
    except (OSError, ValueError):
        return False


def demo():
    """Built once per server (the first viewer watches each stage run), then read from disk by all.
    The lock keeps two first viewers from building into one folder."""
    if "demo" in st.session_state:
        return st.session_state.demo
    if _already_built():
        with _build_lock():
            st.session_state.demo = build(ROOT, N_PEOPLE, SEED)
        return st.session_state.demo
    with st.status(f"Building a city of {N_PEOPLE:,} people", expanded=True) as s:
        st.caption("Once per server, about a minute. Every stage below is the real pipeline.")
        with _build_lock():
            d = build(ROOT, N_PEOPLE, SEED, progress=lambda stage: s.write(STAGES.get(stage, stage)))
        s.update(label=f"City built: {N_PEOPLE:,} people", state="complete", expanded=False)
    st.session_state.demo = d
    return d


@st.cache_data(show_spinner=False)
def outcomes(_d, key):
    with Q.connect(_d) as con:
        return Q.identity_outcomes(con, _d.run)


@st.cache_data(show_spinner=False)
def households(_d, key):
    with Q.connect(_d) as con:
        return Q.household_outcomes(con, _d.run)


@st.cache_data(show_spinner=False)
def candidates(_d, key):
    with Q.connect(_d) as con:
        return Q.deletion_candidates(con, _d.run, outcomes(_d, key))


@st.cache_data(show_spinner=False)
def checks(_d, key):
    with Q.connect(_d) as con:
        return O.foundation_checks(con, _d.run, _d.summary)


@st.cache_data(show_spinner=False)
def base_scores(_d, key):
    with Q.connect(_d) as con:
        return O.identity_scores(con, _d.run)


def key(d) -> str:
    return f"{d.n_people}-{d.seed}"


def num(x, digits: int = 3) -> str:
    """Round half up, as the README does (0.9105 is 91.1%, not the 91.0% binary floats give)."""
    return str(Decimal(str(x)).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))


def pct(x, digits: int = 1) -> str:
    return num(100 * Decimal(str(x)), digits) + "%"


def run_for(label: str) -> dict:
    return next(r for r in Q.saved_results()["runs"] if r["people"] == SIZES[label])


def header(title: str, lede: str):
    st.title(title)
    st.markdown(f'<p class="lede">{html.escape(lede)}</p>', unsafe_allow_html=True)


def how(text: str):
    with st.popover("How it works"):
        st.markdown(text)


def verdict(ok: bool, text: str):
    st.markdown(f'<div class="verdict {"ok" if ok else "bad"}">{"✓" if ok else "✗"} {html.escape(text)}</div>',
                unsafe_allow_html=True)


def stat(label: str, value: str, small: str = "", status: str | None = None):
    dot = f'<span class="dot {status}"></span>' if status else ""
    st.markdown(f'<div class="mono">{dot}{html.escape(label)}</div><div class="big">{html.escape(value)} '
                f'<small>{html.escape(small)}</small></div>', unsafe_allow_html=True)


def log(action: str, result):
    LOG.add(st.session_state.setdefault("visitor", f"visitor-{uuid.uuid4().hex[:4]}"), action, result)


# ----------------------------------------------------------------- Observe
def control_room():
    d = demo()
    header("Customer data that checks its own answers",
           "A live city where the truth is known. Six checks every AI marketing decision depends on.")
    rows = checks(d, key(d))
    cols = st.columns(3)
    for i, c in enumerate(rows):
        with cols[i % 3].container(key=f"card_{c['id']}"):
            value = num(c["value"]) if isinstance(c["value"], float) else f"{c['value']:,}"
            stat(c["title"], value, f"of {c['of']:,}" if c["of"] else "", c["status"])
            st.markdown(f'<div class="note">{html.escape(c["unit"])}. {html.escape(c["note"])}.</div>', unsafe_allow_html=True)
            with st.popover("Evidence", width="stretch"):
                st.caption(f"{len(c['evidence']):,} rows behind this number")
                st.dataframe(c["evidence"].head(500), hide_index=True, width="stretch")

    st.subheader("Find the failure, fix it, prove it")
    steps = [("failure_lab", "Break a sync", "Throttle, error and crash the store API. Count what arrived."),
             ("identity", "Fix a wrong merge", "Split it out. Watch precision change against the answer key."),
             ("deletion", "Forget someone", "Delete a person, then re-send every file. Nobody comes back."),
             ("audiences", "Build an audience", "Describe it in words. See the rules, the count and who consent excludes.")]
    c = st.columns(4)
    for col, (page, title, text) in zip(c, steps):
        with col.container(key=f"card_step_{page}"):
            st.markdown(f'<div class="mono">step {steps.index((page, title, text)) + 1}</div>', unsafe_allow_html=True)
            st.page_link(PAGES[page], label=title)
            st.caption(text)

    with st.container(key="band"):
        size = st.segmented_control("Measured at", list(SIZES), default="1M", key="home_size") or "1M"
        r = run_for(size)
        b = st.columns(4)
        with b[0]:
            stat("Identity F1 (pairs)", num(r["identity"]["f1"]))
        with b[1]:
            stat("Household F1", num(r["households_f1"]))
        with b[2]:
            stat("Wrongly deleted", "1 → 0" if size == "1M" else str(r["deletion"]["records_of_people_who_never_asked"]), "records")
        with b[3]:
            stat("Pipeline", f"{r['timing']['pipeline_minutes']} min" if "timing" in r else "not timed", r["machine"])
    how("One generator makes a city: messy records from four sources, and an answer key the pipeline never sees. "
        "Every check above runs live on this city. Pairwise scores count pairs of records; "
        "\"resolved exactly\" counts whole people. "
        "At 1M, wrongly deleted records went 85 → 1 (phone rule) → 0 (date-of-birth rule, re-planned on the same run). "
        f"[Source code]({REPO_URL})")


def scale():
    header("Scale", "The same command at four sizes. Click a stage in the legend.")
    res = Q.saved_results()
    timed = [r for r in res["runs"] if "timing" in r]
    stages = ["store_build", "store_sync", "ingest", "identity", "person_deletion", "products", "model", "more_models", "experiment"]
    label = {"store_build": "store build", "store_sync": "store API sync", "person_deletion": "deletion",
             "products": "dbt products", "model": "home-buyer model", "more_models": "income and seller models"}
    names = [label.get(k, k) for k in stages]
    data = pd.DataFrame([{"People": f"{r['people']:,}", "Stage": label.get(k, k), "Seconds": v, "step": stages.index(k)}
                         for r in timed for k, v in r["timing"]["seconds"].items()])
    sel = alt.selection_point(fields=["Stage"], bind="legend")
    chart = alt.Chart(data).mark_bar().encode(
        y=alt.Y("People:N", sort=[f"{r['people']:,}" for r in timed], title=None),
        x=alt.X("sum(Seconds):Q", title="Seconds"),
        color=alt.Color("Stage:N", sort=names, title=None, legend=alt.Legend(orient="right"),
                        scale=alt.Scale(domain=names, range=["#D6DCF5", "#AAB6EC", "#7C8FE0", BLUE, RED, GREEN,
                                                             "#F1D592", GOLD, NAVY])),
        opacity=alt.condition(sel, alt.value(1), alt.value(0.15)),
        order=alt.Order("step:Q"), tooltip=["People", "Stage", "Seconds"]).add_params(sel).properties(height=alt.Step(56))
    st.altair_chart(chart, width="stretch")
    c = st.columns(len(timed))
    for col, r in zip(c, timed):
        with col:
            stat(f"{r['people']:,} people", f"{r['timing']['pipeline_minutes']} min", f"{r['timing']['peak_memory_gb']} GB peak")
    with st.expander("Quality at every size"):
        st.dataframe(pd.DataFrame([{"People": f"{r['people']:,}", "Identity P / R (pairs)": f"{pct(r['identity']['precision'])} / {pct(r['identity']['recall'])}",
                                    "Deleted people with data left": f"{r['deletion']['people_with_data_left']} of {r['deletion']['requested']:,}",
                                    "Households F1": num(r["households_f1"]),
                                    "Home-buyer AUC (ceiling)": f"{num(r['home_buyer_auc'])} ({num(r['home_buyer_ceiling_auc'])})",
                                    "Income within one band (zip only)": f"{pct(r['income_within_one_band'])} ({pct(r['income_zip_only'])})"}
                                   for r in res["runs"]]), hide_index=True, width="stretch")
    how(res["note"] + " What broke on the way up, and how each break was fixed at its root, is in the README.")


# ----------------------------------------------------------------- Investigate
def identity():
    d = demo()
    header("Identity graph", "Pick anyone. See every record, why they were joined, and fix it if it is wrong.")
    o = outcomes(d, key(d))
    show = Q.showcase(o, k=40)
    p = Q.people(d.run).set_index("person_id")
    label = lambda pid: f"{p.at[pid, 'first_name']} {p.at[pid, 'last_name']}"
    kinds = {"Found despite typos": "correct", "Split in two": "split", "Merged with someone": "merged"}
    a, b = st.columns([3, 1], vertical_alignment="bottom")
    with a:
        kind = st.segmented_control("Show", list(kinds), default="Merged with someone", key="id_kind") or "Merged with someone"
    pool = show[kinds[kind]]
    if not pool:
        st.info("Nobody of that kind in this city.")
        return
    with b:
        if st.button("🎲 Surprise me", width="stretch"):
            st.session_state.id_pick = random.choice(pool)
    if st.session_state.get("id_pick") not in pool:
        st.session_state.id_pick = pool[0]
    pid = st.selectbox("Person (type to search)", pool, format_func=label, key="id_pick")
    row = o.set_index("person_id").loc[pid]
    with Q.connect(d) as con:
        recs = Q.person_records(con, d.run, pid)
        strangers = Q.strangers_in(con, d.run, row["keys"], pid)
        evidence = O.match_evidence(con, list(row["keys"]))
    truth = Q.person_truth(d.run, pid)
    if row["verdict"] == "correct":
        verdict(True, f"{row['records']} records, one person. Nobody else mixed in.")
    elif row["verdict"] == "split":
        verdict(False, f"Split: their records landed in {row['persons_found']} persons.")
    else:
        verdict(False, "Merged: someone else's record was joined to them.")
    # a segmented control, not st.tabs: tabs reset to the first one on every rerun, so a split would
    # throw the visitor back to the graph instead of showing what changed
    view = st.segmented_control("View", ["Graph", "Why they were joined", "Fix it"], default="Graph", key="id_view") or "Graph"
    if view == "Graph":
        st.graphviz_chart(Q.person_dot(recs, strangers, truth["name"]))
        st.caption("The pipeline's decision as built. Dashed boxes: its persons. Gold lines: whose record it really is. "
                   "Red: someone else's. Edits in \"Fix it\" are scored on a private copy.")
    elif view == "Why they were joined":
        if evidence.empty:
            st.info("No scored pairs inside these persons.")
        else:
            colors = {"agree": f"color: {GREEN}", "differ": f"color: {RED}; font-weight: 600", "missing": f"color: {MUTED}"}
            st.dataframe(evidence.style.map(lambda v: colors.get(v, ""), subset=list(O.FIELDS)), hide_index=True, width="stretch")
            st.caption("Probability: the matcher's belief that the two records are one person. Fields: agree, differ, or missing on one side.")
    else:
        fix_identity(d, pid, row, recs, strangers)


def fix_identity(d, pid, row, recs, strangers):
    edits = st.session_state.setdefault("edits", O.IdentityEdits())
    base = base_scores(d, key(d))
    with Q.connect(d) as con:
        now = O.identity_scores(con, d.run, edits.overrides) if edits.overrides else base
    c = st.columns(3)
    delta = lambda k: None if now[k] == base[k] else f"{(now[k] - base[k]) * 100:+.2f} pts"
    c[0].metric("Precision (pairs)", num(now["precision"], 4), delta("precision"))
    c[1].metric("Recall (pairs)", num(now["recall"], 4), delta("recall"))
    c[2].metric("People resolved exactly", pct(now["people_resolved_exactly"], 2), delta("people_resolved_exactly"))
    st.caption(f"Scored over all {now['true_pairs']:,} true pairs and {now['people']:,} people in the city, against the answer key.")
    with Q.connect(d) as con:      # a store order is matched as part of its customer profile
        profile = dict(con.execute("SELECT order_id, profile_id FROM identity_store_profiles").fetchall())
    me = Q.person_truth(d.run, pid)["name"]
    seen = set()
    for s in strangers.itertuples():
        uid = f"store:{profile.get(s.record_id, s.record_id)}" if s.source == "store" else f"{s.source}:{s.record_id}"
        if uid in edits.overrides or uid in seen:
            continue
        seen.add(uid)
        whose = f"the other {s.really}" if s.really == me else s.really
        if st.button(f"Split out {whose}'s {s.source} record", key=f"split_{uid}", type="primary"):
            edits.split(uid)
            log("identity: split", {"record": uid, "person": pid})
            st.rerun()
    keys = [k for k in row["keys"]]
    if len(keys) > 1 and st.button(f"Merge their {len(keys)} persons into one", key=f"merge_{pid}", type="primary"):
        for k in keys[1:]:
            with Q.connect(d) as con:
                edits.merge(con, keys[0], k)
        log("identity: merge", {"persons": keys})
        st.rerun()
    if row["verdict"] == "correct" and not len(strangers):
        st.caption("Nothing to fix for this person. Try \"Merged with someone\" or \"Split in two\".")
    if edits.history and st.button("Undo last change", key="undo"):
        edits.undo()
        log("identity: undo", {"edits_left": len(edits.history)})
        st.rerun()
    how("Edits live on a private overlay of the identity map; the city never changes. Each edit is rescored on "
        "every record in the city: pairwise precision and recall count pairs of records, \"resolved exactly\" "
        "counts people whose records form one cluster holding nobody else.")


def failure_lab():
    d = demo()
    header("Ingestion failure lab", "Make the store's API misbehave. Then count what arrived.")
    c = st.columns(4)
    p429 = c[0].slider("Throttled (429)", 0, 40, 20, format="%d%%") / 100
    p503 = c[1].slider("Server errors (503)", 0, 40, 10, format="%d%%") / 100
    pdrop = c[2].slider("Dropped connections", 0, 40, 10, format="%d%%") / 100
    crash = c[3].toggle("Crash mid-sync", value=True)
    at = st.select_slider("Crash after page", options=list(range(1, 11)), value=3, disabled=not crash)
    if st.button("Run the sync", type="primary"):
        with st.status("Syncing every order through the faulty API", expanded=True) as s:
            r = O.failure_lab(d, p429, p503, pdrop, at if crash else None)
            for e in r["events"]:
                s.write(e)
            s.update(label=f"Done in {r['seconds']} s", state="complete", expanded=False)
        st.session_state.lab = r
        log("ingestion: fault lab", {k: r[k] for k in ("expected", "received", "missing", "retries", "resumed")})
    r = st.session_state.get("lab")
    if not r:
        return
    ok = r["missing"] == 0 and r["copies"] == r["received"] == r["expected"]
    verdict(ok, f"{r['received']:,} of {r['expected']:,} orders, each once." if ok else f"{r['missing']} orders missing.")
    k = st.columns(4)
    with k[0]:
        stat("Store holds", f"{r['expected']:,}", "orders")
    with k[1]:
        stat("Received", f"{r['received']:,}", f"{r['copies'] - r['received']} duplicates")
    with k[2]:
        stat("Requests", f"{r['requests']:,}", f"{r['retries']} retried")
    with k[3]:
        stat("Resumed", "yes" if r["resumed"] else "no", "from saved cursor" if r["resumed"] else "")
    if r["errors"]:
        st.markdown('<div class="mono" style="margin-top:1.4rem">Retries by failure</div>', unsafe_allow_html=True)
        names = {"429": "Throttled (429)", "503": "Server error (503)", "IncompleteRead": "Dropped connection"}
        err = pd.DataFrame({"Failure": [names.get(str(k), str(k)) for k in r["errors"]], "Retries": list(r["errors"].values())})
        st.altair_chart(alt.Chart(err).mark_bar(color=BLUE, cornerRadiusEnd=4).encode(
            y=alt.Y("Failure:N", title=None, axis=alt.Axis(labelFontSize=13, labelLimit=220)),
            x=alt.X("Retries:Q", title="Retries", axis=alt.Axis(tickMinStep=1, format="d")), tooltip=["Failure", "Retries"]).properties(height=alt.Step(34)),
            width="stretch")
    how("A real HTTP server serves the store's orders with the faults you set. The connector backs off and retries, "
        "honours Retry-After, writes each page atomically and saves its cursor after every page. A crash kills the sync; "
        "the second run resumes from the cursor. Then the received orders are reconciled against what the store holds.")


def deletion():
    d = demo()
    header("Privacy center", "Every person here shares a home and a last name with someone else. Delete one. Nobody else should go.")
    cands = candidates(d, key(d))
    p = Q.people(d.run).set_index("person_id")
    a, b = st.columns([3, 1], vertical_alignment="bottom")
    with a:
        pid = st.selectbox("Person (type to search)", cands, format_func=lambda i: f"{p.at[i, 'first_name']} {p.at[i, 'last_name']}",
                           key="del_pick")
    with b:
        go = st.button("Forget them", type="primary", width="stretch")
    if go:
        with Q.connect(d) as con:
            before = Q.person_records(con, d.run, pid)
        with st.status("Deleting", expanded=True) as s:
            s.write("Request received: a hash of their email and phone, nothing else")
            r = Q.deletion_sandbox(d, pid)
            recs = r["records"]
            for text in (f"Purged at load: {int((recs.removed_by == 'purge at load').sum())} records carried their email or phone",
                         f"Found through the identity graph: {int((recs.removed_by == 'identity graph').sum())} more",
                         f"Checked against the answer key: {r['innocent']} records of anyone else"):
                time.sleep(0.4)
                s.write(text)
            s.update(label="Done", state="complete", expanded=False)
        st.session_state.del_result = (pid, r, before)
        st.session_state.pop("replay", None)
        log("privacy: delete", {"person": pid, "removed": r["removed"], "left": r["left_behind"], "innocent": r["innocent"]})
        st.toast(f"{r['removed']} records gone. {r['innocent']} of anyone else.", icon="🗑️")
    res = st.session_state.get("del_result")
    if not res or res[0] != pid:
        return
    _, r, before = res
    verdict(r["left_behind"] == 0 and r["innocent"] == 0,
            f"{r['removed']} removed, {r['left_behind']} left behind, {r['innocent']} of anyone else.")
    names = dict(zip(before.record_id, before.name))
    cards = []
    for x in r["records"].itertuples():
        cls = "innocent" if x.belongs_to == "SOMEONE ELSE" else ("left" if x.removed_by == "NOT REMOVED" else "gone")
        step = {"purge at load": "purged at load", "identity graph": "found via identity",
                "NOT REMOVED": "left behind"}.get(x.removed_by, x.removed_by)
        cards.append(f'<div class="card {cls}"><b>{html.escape(x.kind)} · {html.escape(step)}</b>'
                     f'{html.escape(str(names.get(x.record_id, "")) or x.record_id)}</div>')
    st.markdown(f'<div class="cards">{"".join(cards)}</div>', unsafe_allow_html=True)
    if len(r["review"]):
        st.warning(f"{len(r['review'])} case(s) sent to a person to decide.")
    st.subheader("Prove it stays deleted")
    if st.button("Re-send every original file", type="primary"):
        with st.spinner("Delivering every source file again, as a sender might"):
            rp = O.replay_after_deletion(d, pid)
        st.session_state.replay = rp
        log("privacy: replay", rp)
    rp = st.session_state.get("replay")
    if rp:
        verdict(rp["came_back"] == 0, f"{rp['files_redelivered']} files re-sent. {rp['came_back']} of their records came back.")
    how("Purge at load removes every row carrying the requester's email or phone. The rest (a vendor row with a "
        "misspelled email, a CRM row with no phone) is found through the identity graph, and only when two keys "
        "agree and nothing (a different phone, a different date of birth) says it is someone else. The replay "
        "re-delivers every file under a new name; tombstones and the suppression list keep the person out. "
        "All of it runs on a private copy: the city never changes.")


def households_page():
    d = demo()
    header("Households", "Click a home. Colours are the real households.")
    h = households(d, key(d))
    c = st.columns(3)
    with c[0]:
        stat("Homes built", f"{len(h):,}")
    with c[1]:
        stat("All from one real home", pct((h.real_households == 1).mean(), 2), f"of {len(h):,}")
    with c[2]:
        stat("Household F1 (pairs)", num(d.summary["products"]["household_f1"]))
    kind = st.segmented_control("Show", ["Built right", "Two homes joined"], default="Built right", key="hh_kind") or "Built right"
    multi = h[(h.members > 1) & ((h.real_households == 1) == (kind == "Built right"))].sort_values(
        ["members", "household_key"], ascending=[False, True]).head(80)
    if multi.empty:
        st.info("No home of that kind in this city.")
        return
    left, right = st.columns([2, 3], gap="large")
    with left:
        table = multi[["address", "members"]].rename(columns={"address": "Home", "members": "People"})
        ev = st.dataframe(table, hide_index=True, width="stretch", height=420, on_select="rerun",
                          selection_mode="single-row", key="hh_table")
        rows = ev.selection.rows if ev and ev.selection else []
    pick = multi.iloc[rows[0] if rows else 0]
    with Q.connect(d) as con:
        m = Q.household_members(con, d.run, pick.household_key)
    with right:
        n = m.real_household.nunique()
        verdict(n == 1, "One real household." if n == 1 else f"{n} real households joined.")
        st.graphviz_chart(Q.household_dot(m, pick.address))
        if m.counted_twice.any():
            st.caption("Counted twice: identity split one person in two, so the head count is one too high.")
    how("After identity, each person gets a current address. People at the same address and unit form a household. "
        "Precision 99.98% at 100,000 people, counted over pairs of members.")


# ----------------------------------------------------------------- Decide
PRESETS = {"Lapsed big spenders": "lapsed high-value customers by email",
           "Movers to welcome": "customers who moved, by mail",
           "Affluent prospects": "affluent prospects by email"}


def audiences():
    d = demo()
    header("Audience builder", "Describe who you want. See the rules, the count, and who consent keeps out.")
    c = st.columns(len(PRESETS))
    for col, (name, text) in zip(c, PRESETS.items()):
        if col.button(name, key=f"preset_{name}", width="stretch"):
            st.session_state.aud_text = text
    text = st.text_input("Describe the audience", key="aud_text",
                         placeholder="e.g. lapsed high-value customers we can email")
    rules, understood = O.parse_audience(text or "")
    chips = "".join(f'<span class="chip blue">{html.escape(O.FLAGS[f])}{"" if v else " = no"}</span>' for f, v in rules["flags"].items())
    if rules["income_min"]:
        chips += f'<span class="chip blue">income band ≥ {rules["income_min"]}</span>'
    if rules["channel"]:
        chips += f'<span class="chip">{O.CHANNELS[rules["channel"]]} only</span>'
    st.markdown(f'<div class="mono">Interpreted as</div>{chips or "<span class=chip>everyone</span>"}', unsafe_allow_html=True)
    with st.expander("Edit the rules"):
        flags = st.pills("Must be", list(O.FLAGS), default=[f for f, v in rules["flags"].items() if v],
                         format_func=O.FLAGS.get, selection_mode="multi", key=f"flags_{text}")
        income = st.select_slider("Income band at least", options=[None, 1, 2, 3, 4, 5], value=rules["income_min"],
                                  format_func=lambda v: "any" if v is None else str(v), key=f"inc_{text}")
        channel = st.segmented_control("Reach by", list(O.CHANNELS), default=rules["channel"], format_func=O.CHANNELS.get,
                                       key=f"ch_{text}")
        negatives = {f: v for f, v in rules["flags"].items() if v is False}
        rules = {"flags": {**{f: True for f in flags}, **negatives}, "income_min": income, "channel": channel}
    with Q.connect(d) as con:
        a = O.audience(con, rules)
    k = st.columns(3)
    with k[0]:
        stat("Segment", f"{a['segment']:,}", f"of {a['population']:,} people")
    with k[1]:
        stat("Reachable", f"{a['reachable']:,}", O.CHANNELS.get(rules["channel"], "any channel"))
    with k[2]:
        stat("Kept out", f"{len(a['excluded']):,}", "for consent or missing contact")
    st.write("")
    left, right = st.columns([1, 1], gap="large")
    with left:
        if len(a["excluded"]):
            reasons = a["excluded"].reason.value_counts().rename_axis("Reason").reset_index(name="People")
            st.altair_chart(alt.Chart(reasons).mark_bar(color=GOLD, cornerRadiusEnd=4).encode(
                y=alt.Y("Reason:N", title=None, axis=alt.Axis(labelLimit=260, labelFontSize=13)),
                x=alt.X("People:Q", axis=alt.Axis(tickMinStep=1, format="d")),
                tooltip=["Reason", "People"]).properties(height=alt.Step(34)),
                width="stretch")
        st.markdown('<div class="mono">Members: click one</div>', unsafe_allow_html=True)
        mem = a["members"].assign(Name=lambda x: (x.first_name + " " + x.last_name).str.title())[["Name", "person_key"]]
        ev = st.dataframe(mem.head(500), hide_index=True, width="stretch", height=300, on_select="rerun",
                          selection_mode="single-row", key=f"aud_members_{text}", column_order=["Name"])
        sel = ev.selection.rows if ev and ev.selection else []
    with right:
        if len(mem):
            pk = mem.iloc[sel[0] if sel else 0].person_key
            st.markdown(f'<div class="mono">Why {html.escape(mem.set_index("person_key").at[pk, "Name"])} is in</div>',
                        unsafe_allow_html=True)
            with Q.connect(d) as con:
                prov = O.provenance(con, pk)
            prov = prov.assign(seen=prov.seen.astype(str).str[:10])
            st.dataframe(prov[["attribute", "value", "source", "seen"]], hide_index=True, width="stretch",
                         column_config={"attribute": st.column_config.TextColumn("Field", width="small"),
                                        "value": st.column_config.TextColumn("Value"),
                                        "source": st.column_config.TextColumn("Source", width="small"),
                                        "seen": st.column_config.TextColumn("Seen", width="small")})
            st.caption("Every value with the source it came from and when that source last saw it.")
    if st.button("Save this audience", type="primary"):
        log("audience: saved", {"rules": rules, "segment": a["segment"], "reachable": a["reachable"]})
        st.toast(f"Saved: {a['reachable']:,} reachable people. See the activity log.", icon="✅")
    how("The sentence becomes typed rules (shown as chips) before anything runs; words it does not know change nothing. "
        "Rules run against the activation audience built by dbt. Consent is not optional: the channel filter removes "
        "anyone who unsubscribed, asked to be deleted, or has no usable contact, and says which. A person whose value "
        "is unknown is in neither \"homeowners\" nor \"renters\".")


def experiment():
    header("Did the email work?", "Half the inboxes got it. The answer key knows what it really did.")
    size = st.segmented_control("City size", list(SIZES), default="1M", key="exp_size") or "1M"
    e = run_for(size)["experiment"]
    c = st.columns(4)
    with c[0]:
        stat("Real effect", f"+{num(100 * Decimal(str(e['true_effect'])), 2)}", "pts")
    with c[1]:
        stat("Measured", f"+{num(100 * Decimal(str(e['corrected_effect'])), 2)}", "pts, attribution corrected")
    with c[2]:
        stat("Truth inside interval", "yes" if e["truth_inside_corrected_ci"] else "no", status="pass" if e["truth_inside_corrected_ci"] else "fail")
    with c[3]:
        stat("Readout", "trusted" if e["trustworthy"] else "alarm", f"split check p = {e['sample_ratio_p']}",
             status="pass" if e["trustworthy"] else "fail")
    if not e["trustworthy"]:
        st.warning("The group-size check fired here, and the readout refused to vouch for itself. It was right: "
                   "this is the one run where the truth fell outside the interval.")
    st.subheader("Who to send it to next time")
    b = e["buyers_per_1000_emails"]
    bars = pd.DataFrame({"Policy": ["Everyone", "Likeliest buyers", "Uplift model", "Best possible"],
                         "Extra buyers per 1,000 emails": [b["everyone"], b["likeliest_buyers_30pct"],
                                                           b["uplift_model_30pct"], b["best_possible_30pct"]]})
    enc = alt.Chart(bars).encode(x=alt.X("Policy:N", sort=list(bars.Policy), title=None, axis=alt.Axis(labelAngle=0, labelFontSize=13)),
                                 y=alt.Y("Extra buyers per 1,000 emails:Q", title=None))
    st.altair_chart((enc.mark_bar(cornerRadiusTopLeft=6, cornerRadiusTopRight=6).encode(
        color=alt.Color("Policy:N", legend=None, scale=alt.Scale(domain=list(bars.Policy), range=["#D6DCF5", "#7C8FE0", BLUE, GOLD])),
        tooltip=["Policy", alt.Tooltip("Extra buyers per 1,000 emails:Q", format=".1f")])
        + enc.mark_text(dy=-10, fontSize=15, fontWeight=500, color=INK).encode(
            text=alt.Text("Extra buyers per 1,000 emails:Q", format=".0f"))).properties(height=280), width="stretch")
    st.caption("Extra buyers per 1,000 emails, emailing 30% of inboxes. Measured runs; a few thousand people are too few to test an email.")
    how("Inboxes are split by a hash of the address, so nobody can choose. The answer key knows what each person "
        "would have done with and without the email, so the measured effect can be checked. An uplift model learns "
        "who the email changes, using only what was known before the send.")


def agent():
    d = demo()
    header("Operations agent", "It quotes the docs, reads the live warehouse, and changes nothing without a second person.")
    if "agent_dir" not in st.session_state:
        st.session_state.agent_dir = Path(tempfile.gettempdir()) / f"plumbline-agent-{uuid.uuid4().hex[:12]}"
        st.session_state.chat, st.session_state.pending = [], []
    ctx = Q.agent_context(d, st.session_state.agent_dir, use_model=USE_MODEL)
    roles = Q.example_roles()
    who = st.segmented_control("You are", list(roles), default="U0ADM0001", key="agent_who",
                               format_func=lambda u: f"{u} · {', '.join(roles[u])}") or "U0ADM0001"
    from plumbline.agents.agent import handle

    def ask(user: str, q: str):
        st.session_state.chat.append({"role": "user", "text": f"**{user}:** {q}"})
        r = handle(ctx, user, q, channel="app")
        meta = " · ".join(x for x in [r.skill, r.tool, ", ".join(r.sources), "refused" if r.refused else ""] if x)
        st.session_state.chat.append({"role": "assistant", "text": r.text, "meta": meta})
        log("agent: " + (r.tool or r.skill or "answer"), {"user": user, "asked": q, "refused": r.refused})
        if r.proposals and r.skill != "approvals":
            st.session_state.pending += [(pid, user) for pid in r.proposals]

    q = st.pills("Try", ["Is the pipeline healthy?", "What's in the deletion review queue?", "What does one_key_matches mean?",
                         "What is our parental leave policy?", "Give U0NEW0001 the privacy role"],
                 key="agent_pill", selection_mode="single")
    if q and st.session_state.get("agent_last_pill") != q:
        st.session_state.agent_last_pill = q
        ask(who, q)
    for m in st.session_state.chat[-12:]:
        with st.chat_message(m["role"]):
            st.markdown(m["text"])
            if m.get("meta"):
                st.caption(m["meta"])
    for pid, asker in list(st.session_state.pending):
        approver = next((u for u, r in roles.items() if "admin" in r and u != asker), None)
        if approver and st.button(f"Approve {pid} as {approver}", key=f"ap_{pid}", type="primary"):
            st.session_state.pending.remove((pid, asker))
            ask(approver, f"approve {pid}")
            st.rerun()
    if typed := st.chat_input("Ask anything about the pipeline or the runbooks"):
        ask(who, typed)
        st.rerun()
    how("Questions are routed to a skill. Answers quote the runbooks and cards with their source, and refuse when "
        "the documents do not say. Status questions run read-only tools on the warehouse. A change is only proposed: "
        "a second person with the right role must approve it, and nobody can approve their own request. "
        + ("" if USE_MODEL else "No language model is called on this public page."))


def activity():
    header("Activity log", "Everything done in this app, by whom, and what it found.")
    r = LOG.recent(200)
    if r.empty:
        st.info("Nothing yet. Run a sync, fix an identity, forget someone or save an audience.")
        return
    r = r.assign(result=r.result.map(lambda x: json.dumps(x) if not isinstance(x, str) else x))
    st.dataframe(r.rename(columns={"at": "When", "who": "Who", "action": "Action", "result": "Result"}),
                 hide_index=True, width="stretch", height=min(560, 35 * (len(r) + 1) + 3))


PAGES = {"home": st.Page(control_room, title="Control room", icon="📏", default=True),
         "scale": st.Page(scale, title="Scale", icon="⏱️", url_path="scale"),
         "identity": st.Page(identity, title="Identity graph", icon="🔍", url_path="identity"),
         "failure_lab": st.Page(failure_lab, title="Failure lab", icon="⚡", url_path="failure-lab"),
         "deletion": st.Page(deletion, title="Privacy center", icon="🗑️", url_path="deletion"),
         "households": st.Page(households_page, title="Households", icon="🏠", url_path="households"),
         "audiences": st.Page(audiences, title="Audience builder", icon="🎯", url_path="audiences"),
         "experiment": st.Page(experiment, title="Email experiment", icon="📈", url_path="experiment"),
         "agent": st.Page(agent, title="Operations agent", icon="💬", url_path="agent"),
         "activity": st.Page(activity, title="Activity log", icon="🧾", url_path="activity")}

if __name__ == "__main__":
    st.navigation({"Observe": [PAGES["home"], PAGES["scale"]],
                   "Investigate": [PAGES["identity"], PAGES["failure_lab"], PAGES["deletion"], PAGES["households"]],
                   "Decide": [PAGES["audiences"], PAGES["experiment"], PAGES["agent"], PAGES["activity"]]}).run()
