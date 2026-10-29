"""Plumbline, live: a synthetic city built in front of you, every answer next to its answer key.

Run it with the project's Python:  python -m streamlit run streamlit_app.py
The page only draws. Every number comes from plumbline.app.queries (tested without Streamlit) or
from docs/results/scale.json (measured runs at 20,000 to 1,000,000 people).
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

INK, SLATE, BRASS, GREEN, BRICK = "#17202E", "#5E6878", "#A8741A", "#2E7A68", "#B3432F"
st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,400;6..72,600&family=Public+Sans:wght@400;500;600&display=swap');
html, body, [class*="css"], .stMarkdown, p, li, label, input, textarea, button {{ font-family: 'Public Sans', sans-serif; }}
h1, h2, h3 {{ font-family: 'Newsreader', serif !important; font-weight: 600 !important; color: {INK}; letter-spacing: -0.01em; }}
h1 {{ font-size: 2.5rem !important; line-height: 1.1 !important; }}
.block-container {{ max-width: 1180px; padding-top: 2rem; }}
.lede {{ font-size: 1.1rem; color: {SLATE}; margin: -0.4rem 0 1.2rem 0; }}
[data-testid="stMetricValue"] {{ font-family: 'Newsreader', serif; font-variant-numeric: tabular-nums; }}
.cards {{ display: flex; flex-wrap: wrap; gap: 10px; margin: 0.4rem 0 1rem 0; }}
.card {{ border: 1.5px solid #C9CED6; border-radius: 10px; padding: 8px 12px; background: white; min-width: 150px;
        font-size: 0.86rem; line-height: 1.35; }}
.card b {{ display: block; font-size: 0.78rem; color: {SLATE}; font-weight: 600; }}
.card.gone {{ background: #EEF5F2; border-color: {GREEN}; color: {SLATE}; text-decoration: line-through; }}
.card.left {{ border-color: {BRICK}; background: #FBEDEA; }}
.card.innocent {{ border-color: {BRICK}; background: #F8E1DC; font-weight: 600; }}
.verdict {{ font-size: 1.15rem; font-weight: 600; margin: 0.3rem 0 0.8rem 0; }}
.ok {{ color: {GREEN}; }} .bad {{ color: {BRICK}; }}
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

from plumbline.app import queries as Q  # noqa: E402
from plumbline.app.demo import STAGES, build  # noqa: E402

N_PEOPLE = int(os.environ.get("PLUMBLINE_DEMO_PEOPLE", "4000"))
SEED = 11
ROOT = Path(os.environ.get("PLUMBLINE_DEMO_DIR", Path(tempfile.gettempdir()) / "plumbline-demo"))
USE_MODEL = os.environ.get("PLUMBLINE_APP_USE_LLM") == "1"
REPO_URL = "https://github.com/AKilalours/plumbline"
SIZES = {"20k": 20_000, "100k": 100_000, "300k": 300_000, "1M": 1_000_000}


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


def key(d) -> str:
    return f"{d.n_people}-{d.seed}"


def num(x, digits: int = 3) -> str:
    """Round half up, as the README does (0.9105 is 91.1%, not the 91.0% binary floats give)."""
    return str(Decimal(str(x)).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))


def pct(x, digits: int = 1) -> str:
    return num(100 * Decimal(str(x)), digits) + "%"


def run_for(label: str) -> dict:
    return next(r for r in Q.saved_results()["runs"] if r["people"] == SIZES[label])


def how(text: str):
    with st.popover("How it works"):
        st.markdown(text)


def verdict(ok: bool, text: str):
    st.markdown(f'<div class="verdict {"ok" if ok else "bad"}">{"✓" if ok else "✗"} {html.escape(text)}</div>',
                unsafe_allow_html=True)


# ----------------------------------------------------------------- pages
def home():
    st.title("Customer data that checks its own answers")
    st.markdown('<p class="lede">A synthetic city where the truth is known. Every result graded against it.</p>',
                unsafe_allow_html=True)
    size = st.segmented_control("City size", list(SIZES), default="1M", key="home_size") or "1M"
    r = run_for(size)
    c = st.columns(4)
    c[0].metric("Identity F1", num(r["identity"]["f1"]), help="Records joined into the right person, scored pair by pair.")
    c[1].metric("Household F1", num(r["households_f1"]), help="People placed in the home they really share.")
    wrong = r["deletion"]["records_of_people_who_never_asked"]
    c[2].metric("Wrongly deleted", "1 → 0" if size == "1M" else str(wrong),
                help="Records of people who never asked to be deleted. At 1M: 85 before the phone rule, 1 in the "
                     "measured run, 0 when that run is re-planned with the date-of-birth rule.")
    c[3].metric("Pipeline time", f"{r['timing']['pipeline_minutes']} min" if "timing" in r else "not timed",
                help=r["machine"])

    pts = pd.DataFrame([{"People": k, "Identity F1": x["identity"]["f1"], "Household F1": x["households_f1"],
                         "chosen": k == size} for k in SIZES for x in [run_for(k)]])
    long = pts.melt(["People", "chosen"], var_name="Measure", value_name="Score")
    base = alt.Chart(long).encode(x=alt.X("People:N", sort=list(SIZES), title=None, axis=alt.Axis(labelAngle=0)),
                                  y=alt.Y("Score:Q", scale=alt.Scale(domain=[0.85, 1.0]), title=None),
                                  color=alt.Color("Measure:N", scale=alt.Scale(range=[GREEN, BRASS]),
                                                  legend=alt.Legend(orient="top", title=None)),
                                  tooltip=["People", "Measure", alt.Tooltip("Score:Q", format=".3f")])
    st.altair_chart((base.mark_line(strokeWidth=2.5) + base.mark_point(filled=True).encode(
        size=alt.condition("datum.chosen", alt.value(260), alt.value(60)))).properties(height=230), width="stretch")
    st.caption("Flat lines are the point: the answers hold from 20,000 to 1,000,000 people.")

    st.subheader("Try it")
    c = st.columns(4)
    c[0].page_link(PAGES["identity"], label="Find a person", icon="🔍")
    c[1].page_link(PAGES["deletion"], label="Delete someone", icon="🗑️")
    c[2].page_link(PAGES["households"], label="Open a home", icon="🏠")
    c[3].page_link(PAGES["agent"], label="Ask the agent", icon="💬")
    how("1. One generator makes a city: messy records from four sources, and an answer key.\n"
        "2. The pipeline sees only the records: loads them, resolves who is who, carries out deletions, builds households.\n"
        "3. The answer key grades every step. It is never used to train anything.\n\n"
        f"[Source code]({REPO_URL})")


def identity():
    d = demo()
    st.title("Who is who")
    st.markdown('<p class="lede">Pick anyone. See every record the sources sent, and whether they were joined right.</p>',
                unsafe_allow_html=True)
    o = outcomes(d, key(d))
    show = Q.showcase(o, k=40)
    p = Q.people(d.run).set_index("person_id")
    label = lambda pid: f"{p.at[pid, 'first_name']} {p.at[pid, 'last_name']}"
    kinds = {"Found despite typos": "correct", "Split in two": "split", "Merged with someone": "merged"}
    a, b = st.columns([3, 1], vertical_alignment="bottom")
    with a:
        kind = st.segmented_control("Show", list(kinds), default="Found despite typos", key="id_kind") or "Found despite typos"
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
    truth = Q.person_truth(d.run, pid)
    if row["verdict"] == "correct":
        verdict(True, f"{row['records']} records, one person. Nobody else mixed in.")
    elif row["verdict"] == "split":
        verdict(False, f"Split: their records landed in {row['persons_found']} persons.")
    else:
        verdict(False, "Merged: someone else's record was joined to them.")
    st.graphviz_chart(Q.person_dot(recs, strangers, truth["name"]))
    st.caption("Dashed boxes: the pipeline's persons. Gold lines: whose record it really is. Red: someone else's.")
    c = st.columns(3)
    acc = d.summary["identity"]["accuracy"]
    c[0].metric("In this city: right", pct((o.verdict == "correct").mean()))
    c[1].metric("Precision", pct(acc["precision"]))
    c[2].metric("Recall", pct(acc["recall"]))
    with st.expander("Every record, as sent"):
        st.dataframe(recs.rename(columns={"kind": "Source", "record_id": "Record", "name": "Name", "email": "Email",
                                          "phone": "Phone", "address": "Address", "person_key": "Pipeline person"}),
                     hide_index=True, width="stretch")
    how("Records are matched with Splink (probabilistic matching), then clustered so that two people who "
        "clearly differ are never joined. A split costs reach; a wrong merge sends one person's mail to another, "
        "so the matcher prefers to split.")


def deletion():
    d = demo()
    st.title("Forget someone")
    st.markdown('<p class="lede">Every person here shares a home and a last name with someone else. Delete one. '
                'Nobody else should go.</p>', unsafe_allow_html=True)
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
                time.sleep(0.5)
                s.write(text)
            s.update(label="Done", state="complete", expanded=False)
        st.session_state.del_result = (pid, r, before)
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
        who = names.get(x.record_id, "")
        step = {"purge at load": "purged at load", "identity graph": "found via identity",
                "NOT REMOVED": "left behind"}.get(x.removed_by, x.removed_by)
        cards.append(f'<div class="card {cls}"><b>{html.escape(x.kind)} · {html.escape(step)}</b>'
                     f'{html.escape(str(who) or x.record_id)}</div>')
    st.markdown(f'<div class="cards">{"".join(cards)}</div>', unsafe_allow_html=True)
    st.caption("Struck through: deleted. Red: left behind, or someone else's.")
    if len(r["review"]):
        st.warning(f"{len(r['review'])} case(s) sent to a person to decide.")
    how("Purge at load removes every row carrying the requester's email or phone. The rest (a vendor row with a "
        "misspelled email, a CRM row with no phone) is found through the identity graph, and only when two keys "
        "agree and nothing (a different phone, a different date of birth) says it is someone else. "
        "This runs on a private copy: the city never changes.")


def households_page():
    d = demo()
    st.title("Households")
    st.markdown('<p class="lede">Click a home. Colours are the real households.</p>', unsafe_allow_html=True)
    h = households(d, key(d))
    c = st.columns(3)
    c[0].metric("Homes built", f"{len(h):,}")
    c[1].metric("All from one real home", pct((h.real_households == 1).mean(), 2))
    c[2].metric("Household F1", num(d.summary["products"]["household_f1"]))
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
        "Precision 99.98% at 100,000 people: when a home is built, its members really live together.")


def experiment():
    st.title("Did the email work?")
    st.markdown('<p class="lede">Half the inboxes got it. The answer key knows what it really did.</p>',
                unsafe_allow_html=True)
    size = st.segmented_control("City size", list(SIZES), default="1M", key="exp_size") or "1M"
    e = run_for(size)["experiment"]
    c = st.columns(4)
    c[0].metric("Real effect", f"+{num(100 * Decimal(str(e['true_effect'])), 2)} pts")
    c[1].metric("Measured", f"+{num(100 * Decimal(str(e['corrected_effect'])), 2)} pts",
                help="Corrected for orders that could not be traced to an inbox.")
    c[2].metric("Truth inside the interval", "yes" if e["truth_inside_corrected_ci"] else "no")
    c[3].metric("Readout", "trusted" if e["trustworthy"] else "alarm",
                help="A check on group sizes. When it fires, the readout refuses to vouch for itself.")
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
    st.altair_chart((enc.mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
        color=alt.Color("Policy:N", legend=None, scale=alt.Scale(domain=list(bars.Policy), range=["#C9CED6", SLATE, GREEN, BRASS])),
        tooltip=["Policy", alt.Tooltip("Extra buyers per 1,000 emails:Q", format=".1f")])
        + enc.mark_text(dy=-10, fontSize=15, fontWeight=600, color=INK).encode(
            text=alt.Text("Extra buyers per 1,000 emails:Q", format=".0f"))).properties(height=280), width="stretch")
    st.caption("Extra buyers per 1,000 emails, emailing 30% of inboxes. The uplift model targets people the email changes.")
    how("Inboxes are split by a hash of the address, so nobody can choose. The answer key knows what each person "
        "would have done with and without the email, so the measured effect can be checked. An uplift model learns "
        "who the email changes, using only what was known before the send.")


def agent():
    d = demo()
    st.title("Ask the agent")
    st.markdown('<p class="lede">It quotes the docs, reads the live warehouse, and changes nothing without a second '
                'person.</p>', unsafe_allow_html=True)
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
        if r.proposals and r.skill != "approvals":
            st.session_state.pending += [(pid, user) for pid in r.proposals]

    q = st.pills("Try", ["Is the pipeline healthy?", "What does one_key_matches mean?",
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


def scale():
    st.title("Scale")
    st.markdown('<p class="lede">The same command at four sizes. Hover a bar.</p>', unsafe_allow_html=True)
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
        color=alt.Color("Stage:N", sort=names, title="Click a stage", legend=alt.Legend(orient="right"),
                        scale=alt.Scale(domain=names, range=["#C9CED6", "#9AA3B0", "#5E6878", INK, BRICK, GREEN,
                                                             "#D6B271", BRASS, "#7A5212"])),
        opacity=alt.condition(sel, alt.value(1), alt.value(0.15)),
        order=alt.Order("step:Q"), tooltip=["People", "Stage", "Seconds"]).add_params(sel).properties(height=alt.Step(56))
    st.altair_chart(chart, width="stretch")
    c = st.columns(len(timed))
    for col, r in zip(c, timed):
        col.metric(f"{r['people']:,} people", f"{r['timing']['pipeline_minutes']} min",
                   f"{r['timing']['peak_memory_gb']} GB peak", delta_color="off", delta_arrow="off", help=r["machine"])
    with st.expander("Models at every size"):
        st.dataframe(pd.DataFrame([{"People": f"{r['people']:,}",
                                    "Home-buyer AUC (ceiling)": f"{num(r['home_buyer_auc'])} ({num(r['home_buyer_ceiling_auc'])})",
                                    "Income within one band (zip only)": f"{pct(r['income_within_one_band'])} ({pct(r['income_zip_only'])})",
                                    "Work and personal email linked": pct(r["work_personal_link_right"])}
                                   for r in res["runs"]]), hide_index=True, width="stretch")
    how(res["note"] + " What broke on the way up, and how each break was fixed at its root, is in the README.")


PAGES = {"home": st.Page(home, title="Overview", icon="📏", default=True),
         "identity": st.Page(identity, title="Who is who", icon="🔍", url_path="identity"),
         "deletion": st.Page(deletion, title="Forget someone", icon="🗑️", url_path="deletion"),
         "households": st.Page(households_page, title="Households", icon="🏠", url_path="households"),
         "experiment": st.Page(experiment, title="Did the email work?", icon="📈", url_path="experiment"),
         "agent": st.Page(agent, title="Ask the agent", icon="💬", url_path="agent"),
         "scale": st.Page(scale, title="Scale", icon="⏱️", url_path="scale")}

if __name__ == "__main__":
    st.navigation(list(PAGES.values())).run()
