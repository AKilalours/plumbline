"""Ask the data products a question in plain English; get a number, the SQL behind it, and why.

The model writes SQL. Code decides whether it may run, by parsing it (sqlglot), not by trusting it:
one SELECT only; only the data products listed in SCHEMA; no contact column (names, emails, phones,
street lines, dates of birth) anywhere in the query, so an answer is a count, a rate or person keys,
never a contact list; no table functions (read_csv, glob, http); a row cap; a read-only connection
with a time limit. A question that needs any of that gets a refusal with the reason.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

import duckdb
import sqlglot
from pydantic import BaseModel, Field
from sqlglot import exp

from .runtime import NotRecorded, Runtime, Trace

SCHEMA = {
    "products.mart_activation_audience": {
        "_": "one row per person who may be marketed to, with consent per channel",
        "person_key": "the person's id", "email_ok": "may be emailed (consented, not unsubscribed, has an email)",
        "work_email_ok": "may be emailed at work", "phone_ok": "may be called or texted", "mail_ok": "may be sent post",
        "is_customer": "has ordered from the store", "high_value": "top revenue decile", "lapsed": "no order in 180 days",
        "recent_mover": "moved in the last 90 days (confirmed, not vendor-only)", "b2b2c_linked": "work identity linked",
        "income_band": "1 (lowest) to 6", "homeowner": "true, false, or null when unknown",
    },
    "products.dim_household": {
        "_": "one row per household (people at one address)", "household_key": "id", "zip": "5-digit ZIP",
        "members": "number of people", "household_revenue": "store revenue of all members",
    },
    "products.fct_life_events": {
        "_": "detected life events (moves)", "person_key": "id", "event_type": "'move'",
        "detected_at": "when", "to_zip": "destination ZIP of a move", "evidence_source": "which source showed it",
        "confidence": "high, medium or low",
    },
    "products.dim_person": {
        "_": "the golden profile; contact columns are not available here", "person_key": "id", "zip": "current ZIP",
        "address_source": "source of the current address", "address_is_stale": "address older than a year",
        "income_band": "1 to 6", "homeowner": "true/false/null", "orders": "store orders", "revenue": "store revenue",
        "refunds": "refunded orders", "first_order_at": "timestamp", "last_order_at": "timestamp",
        "opens_90d": "email opens, 90 days", "clicks_90d": "email clicks, 90 days", "records": "source records merged",
        "attributes_in_conflict": "attributes the sources disagree on", "household_key": "household id",
        "employer": "employer name", "job_title": "job title",
    },
}
BLOCKED = {"first_name", "last_name", "personal_email", "work_email", "phone", "address_line", "dob",
           "unsubscribed_emails", "person_keys", "email", "street_address", "full_name"}
ALLOWED = {t: {c for c in cols if c != "_"} for t, cols in SCHEMA.items()}
MAX_ROWS, TIMEOUT_S = 200, 10


class SqlAnswer(BaseModel):
    sql: str = Field(default="", description="one DuckDB SELECT over the listed tables, or empty to refuse")
    explanation: str = Field(default="", max_length=400, description="what the query counts, in one or two sentences")
    refuse: str = Field(default="", description="why it cannot be answered with these tables, or empty")


def _schema_text() -> str:
    out = []
    for t, cols in SCHEMA.items():
        out.append(f"{t}: {cols['_']}")
        out += [f"  {c}: {d}" for c, d in cols.items() if c != "_"]
    return "\n".join(out)


SYSTEM = f"""You answer questions about a marketing company's customer data by writing one DuckDB SELECT.
Tables (fully qualified names) and columns:
{_schema_text()}

Rules:
- Use only these tables and columns. Contact details (names, emails, phones, addresses, birth dates)
  are not available: if the question needs them, set refuse.
- "Reachable", "can email", "emailable" mean email_ok = true; consent always comes from mart_activation_audience.
- homeowner can be null (unknown): unknown is neither homeowner nor renter.
- Prefer one row of aggregates. Round rates to 3 decimals.
- Never modify data. Questions that ask to change, delete or export data: set refuse."""


class Rejected(ValueError):
    pass


def check_sql(sql: str) -> str:
    """Return the SQL to run (with a row cap), or raise Rejected with the reason."""
    try:
        trees = sqlglot.parse(sql, read="duckdb")
    except sqlglot.errors.ParseError as e:
        raise Rejected(f"does not parse: {str(e).splitlines()[0][:120]}") from None
    trees = [t for t in trees if t is not None]
    if len(trees) != 1:
        raise Rejected("exactly one statement is allowed")
    t = trees[0]
    if not isinstance(t, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise Rejected(f"only SELECT is allowed, not {t.key.upper()}")
    for bad in (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Command, exp.Copy):
        if t.find(bad):
            raise Rejected(f"{bad.__name__.upper()} is not allowed")
    ctes = {c.alias_or_name.lower() for c in t.find_all(exp.CTE)}
    for tbl in t.find_all(exp.Table):
        if isinstance(tbl.this, exp.Func) or tbl.find(exp.Anonymous):
            raise Rejected("table functions (read_csv, glob, ...) are not allowed")
        name = ".".join(p for p in (tbl.db, tbl.name) if p).lower()
        if tbl.name.lower() in ctes and not tbl.db:
            continue
        if name not in ALLOWED:
            raise Rejected(f"table {name or tbl.sql()} is not one of the data products")
    for f in t.find_all(exp.Func):
        n = (f.sql_name() if not isinstance(f, exp.Anonymous) else f.name).lower()
        if n.startswith(("read_", "glob", "http", "load", "install", "getenv", "pragma", "current_setting")) or \
                n in ("query", "query_table", "columns", "struct_pack", "row", "to_json", "json_object"):
            raise Rejected(f"function {n} is not allowed")
    # Allow-list, not block-list: DuckDB can select columns by pattern (COLUMNS('.*email')) or a whole
    # row by naming a table alias as a column, and neither names a blocked column.
    known = set().union(*ALLOWED.values())
    defined = {a.alias.lower() for a in t.find_all(exp.Alias) if a.alias}
    defined |= {c.name.lower() for cte in t.find_all(exp.CTE) for c in (cte.args.get("alias").columns if cte.args.get("alias") else [])}
    for col in t.find_all(exp.Column):
        n = col.name.lower()
        if n in BLOCKED:
            raise Rejected(f"column {col.name} is contact data and is never returned")
        if n and n not in known and n not in defined:
            raise Rejected(f"{col.name} is not a column of the data products")
    if t.find(exp.Star) and not all(isinstance(s.parent, exp.Count) for s in t.find_all(exp.Star)):
        raise Rejected("SELECT * could return contact data; name the columns")
    if isinstance(t, exp.Select) and not t.args.get("limit"):
        t = t.limit(MAX_ROWS)
    return t.sql(dialect="duckdb")


@dataclass
class Result:
    sql: str
    explanation: str
    rows: list[tuple] | None
    columns: list[str]
    refused: str | None
    trace: Trace | None = None


def run_sql(warehouse, sql: str):
    con = duckdb.connect(str(warehouse), read_only=True)
    timer = threading.Timer(TIMEOUT_S, con.interrupt)
    timer.start()
    try:
        cur = con.execute(sql)
        return [d[0] for d in cur.description], cur.fetchmany(MAX_ROWS)
    finally:
        timer.cancel()
        con.close()


def ask(rt: Runtime, warehouse, question: str) -> Result:
    try:
        ans, trace = rt.structured("ask_data", SYSTEM, f"Question: {question}", SqlAnswer, max_tokens=700)
    except NotRecorded as e:
        return Result("", "", None, [], f"model unavailable: {e}")
    if ans is None:
        return Result("", "", None, [], "the model did not return a valid answer", trace)
    if ans.refuse or not ans.sql.strip():
        return Result("", ans.explanation, None, [], ans.refuse or "the model declined", trace)
    try:
        safe = check_sql(ans.sql)
        cols, rows = run_sql(warehouse, safe)
    except Rejected as e:
        return Result(ans.sql, ans.explanation, None, [], f"blocked: {e}", trace)
    except duckdb.Error as e:
        return Result(ans.sql, ans.explanation, None, [], f"query failed: {str(e).splitlines()[0][:160]}", trace)
    return Result(safe, ans.explanation, rows, cols, None, trace)


# ------------------------------------------------------------------ grading
def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _same(a, b) -> bool:
    x, y = _num(a), _num(b)
    if x is not None and y is not None:
        return abs(x - y) <= max(0.0015, 0.001 * abs(y))
    return str(a).strip().lower() == str(b).strip().lower()


def _row_has(row, want) -> bool:
    """Every value of the gold row appears in the answer row (extra label columns are fine)."""
    pool = list(row)
    for w in want:
        k = next((i for i, v in enumerate(pool) if _same(v, w)), None)
        if k is None:
            return False
        pool.pop(k)
    return True


def same_answer(got: list[tuple], gold: list[tuple], ordered: bool = False) -> bool:
    if got is None or len(got) != len(gold):
        return False
    if ordered:
        return all(_row_has(g, w) for g, w in zip(got, gold))
    left = list(got)
    for w in gold:
        k = next((i for i, g in enumerate(left) if _row_has(g, w)), None)
        if k is None:
            return False
        left.pop(k)
    return True
