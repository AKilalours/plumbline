"""Your own files, through the real pipeline: anyone can bring customer files and get one clean customer list back.

    from plumbline.workspace import read, detect, run
    files = [read("crm.csv", data), read("shop.csv", data2)]
    result = run(files, [detect(f) for f in files])

The steps are the ones Plumbline's own sources go through, applied to files nobody here has seen:

1. read: any delimiter, UTF-8 or Latin-1, at most MAX_ROWS rows a file; every value kept as text.
2. detect: what each column is (first/last/full name, email, phone, street, ZIP, date of birth, record id),
   by the same profiler and rule-based drafter that drafts a new partner's contract. A person can correct it.
3. check: a row that names nobody (no name, email or phone) is held back with that reason; a value that is
   not what its column says (an email without @, a ZIP that is not five digits) is set aside and counted,
   the row stays.
4. match: the probabilistic matcher (Splink, Fellegi-Sunter, learned from the files themselves, no labels)
   with the same safety rules production uses. Files too small or too sparse to learn from fall back to
   exact rules (same email, same phone, same name at the same address), and the result says which ran.
5. golden list: one row per person, each field the value most records agree on, with where it came from.

Nothing is written outside a temporary folder, and nothing is kept: the result lives in the visitor's session.
"""
from __future__ import annotations

import base64
import csv
import io
import json
import re
import tempfile
import time
import zlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

MAX_ROWS = 25_000            # per file: keeps one visitor's run to seconds on a small shared server
MAX_FILES = 5
ROLES = ["first_name", "last_name", "full_name", "email", "phone", "street", "zip", "dob", "record_id", "ignore"]
ROLE_LABEL = {"first_name": "First name", "last_name": "Last name", "full_name": "Full name", "email": "Email",
              "phone": "Phone", "street": "Street address", "zip": "ZIP", "dob": "Date of birth",
              "record_id": "Record id", "ignore": "Not used"}
FIELDS = ["first_name", "last_name", "email", "phone", "street", "zip", "dob"]
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


@dataclass
class File:
    name: str
    df: pd.DataFrame                     # every value as text, "" for empty
    truncated: bool = False
    rows_in_file: int = 0


@dataclass
class Result:
    people: pd.DataFrame                 # the golden list: one row per person
    records: pd.DataFrame                # every usable row with its person_id
    held: pd.DataFrame                   # rows held back, with the reason
    set_aside: dict                      # file -> column -> values not understood
    groups: pd.DataFrame                 # pairs inside each merged person: probability and which fields agree
    method: str                          # "probabilistic" or "rules"
    method_note: str
    seconds: dict = field(default_factory=dict)
    files: list = field(default_factory=list)

    def stats(self) -> dict:
        r, p = self.records, self.people
        by_file = r.groupby("file")["person_id"].agg(["size", "nunique"])
        dupes_inside = int((by_file["size"] - by_file["nunique"]).sum())
        multi = int((p["files"] > 1).sum()) if len(p) else 0
        return {"files": len(self.files), "rows": int(len(r) + len(self.held)), "held": int(len(self.held)),
                "records": int(len(r)), "people": int(len(p)), "merged": int(len(r) - len(p)),
                "duplicates_inside_files": dupes_inside, "in_several_files": multi,
                "reachable_email": int(p["email"].astype(bool).sum()) if len(p) else 0,
                "reachable_phone": int(p["phone"].astype(bool).sum()) if len(p) else 0,
                "no_contact": int((~p["email"].astype(bool) & ~p["phone"].astype(bool)).sum()) if len(p) else 0,
                "set_aside": int(sum(sum(c.values()) for c in self.set_aside.values())),
                "method": self.method, "seconds": round(sum(self.seconds.values()), 1)}


# ------------------------------------------------------------------ read
def read(name: str, data: bytes, max_rows: int = MAX_ROWS) -> File:
    """A CSV (or TSV, or semicolon-separated) file as text columns. Refuses what is not a table."""
    text = None
    for enc in ("utf-8-sig", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if not text or not text.strip():
        raise ValueError(f"{name}: the file is empty")
    try:
        dialect = csv.Sniffer().sniff(text[:20_000], delimiters=",;\t|")
        sep = dialect.delimiter
    except csv.Error:
        sep = ","
    rows = list(csv.reader(io.StringIO(text), delimiter=sep))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if len(rows) < 2 or len(rows[0]) < 2:
        raise ValueError(f"{name}: needs a header row and at least two columns")
    header = _unique([h.strip() or f"column_{i + 1}" for i, h in enumerate(rows[0])])
    body = rows[1: max_rows + 1]
    df = pd.DataFrame([(r + [""] * len(header))[: len(header)] for r in body], columns=header).fillna("")
    df = df.apply(lambda s: s.astype(str).str.strip())
    return File(name=name, df=df, truncated=len(rows) - 1 > max_rows, rows_in_file=len(rows) - 1)


def _unique(names: list[str]) -> list[str]:
    seen, out = Counter(), []
    for n in names:
        seen[n] += 1
        out.append(n if seen[n] == 1 else f"{n}_{seen[n]}")
    return out


# ------------------------------------------------------------------ detect
# Header words seen in real customer exports, in a few languages. Used only here: the partner-file drafter's own
# word list stays generic, because its scores are graded on held-out files and must not be tuned to them.
_SYNONYMS = [
    (r"^(f|first|given|fore)[ _-]?name$|^fname$|^first$|^pr[ée]nom$|^vorname$|^nombre$", "first_name"),
    (r"^(l|last|family|sur)[ _-]?name$|^lname$|^surname$|^last$|^nom$|^nachname$|^apellidos?$", "last_name"),
    (r"^(full[ _-]?)?name$|^(customer|client|contact|member)[ _-]?name$|^nom[ _-]complet$", "full_name"),
    (r"e-?mail|courriel|correo", "email"),
    (r"phone|mobile|cell|tel[eé]?f?|t[ée]l[ée]phone|telefono|handy", "phone"),
    (r"zip|postal|postcode|post[ _-]?code|^plz$|code[ _-]postal|cp$", "zip"),
    (r"birth|dob|birthday|naissance|geburt", "dob"),
    (r"address|street|addr|adresse|direcci[oó]n|strasse|straße", "street"),
]


_ID = re.compile(r"(^|[_ -])(id|no|num|number|key|ref)$|^id[_ -]|_id$|#")


def _by_header(name: str) -> str | None:
    h = name.strip().lower()
    return next((role for rx, role in _SYNONYMS if re.search(rx, h)), None)


def detect(f: File) -> dict[str, str]:
    """Column -> role, by the partner-file drafter (header words and value patterns, no model), then the header
    words above for what it left unplaced. A person can correct any of it before the run."""
    from .ai.onboard import heuristic_draft, profile
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "f.csv"
        f.df.head(300).to_csv(p, index=False)
        prof = profile(p)
    d = heuristic_draft(prof)
    roles = {}
    for c in d.columns:
        roles[c.name] = c.privacy or c.identity or "ignore"
    # a "name" column the header alone could not place: two-word values are a full name
    for c in d.columns:
        if roles[c.name] == "ignore" and c.type == "name":
            roles[c.name] = "full_name"
    # Header words before value patterns run out: in a small file a name column with two distinct values looks like
    # a category, and the drafter's key (the first all-distinct column) was a name, then taken for a record id.
    taken = {r for r in roles.values() if r != "ignore"}
    for c in d.columns:
        guess = _by_header(c.name)
        if roles[c.name] == "ignore" and guess and guess not in taken:
            roles[c.name] = guess
            taken.add(guess)
    ids = [c for c in f.df.columns if roles.get(c) == "ignore" and _ID.search(c.lower())
           and f.df[c].astype(bool).all() and f.df[c].is_unique]
    if ids:
        roles[ids[0]] = "record_id"
    return roles


# ------------------------------------------------------------------ normalise
def _digits(v: str) -> str:
    d = re.sub(r"\D", "", v or "")
    return d[-10:] if len(d) >= 10 else ""


def _zip(v: str) -> str:
    m = re.match(r"^\s*(\d{3,5})(?:-\d{4})?\s*$", v or "")
    return m.group(1).zfill(5) if m else ""


def _dob(v: str) -> str:
    v = (v or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d.%m.%Y", "%Y/%m/%d", "%m-%d-%Y"):
        try:
            d = datetime.strptime(v, fmt)
            return d.strftime("%Y-%m-%d") if 1900 < d.year <= datetime.now().year else ""
        except ValueError:
            continue
    return ""


def _records(f: File, roles: dict[str, str], idx: int) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """-> (records in the matcher's shape, rows held back, values set aside per column)."""
    from .identity.standardize import clean_name, split_full_name
    df, col = f.df, {}
    for c, r in roles.items():
        if r != "ignore" and r not in col and c in df:
            col[r] = c
    get = lambda r: df[col[r]] if r in col else pd.Series([""] * len(df), index=df.index)
    out = pd.DataFrame(index=df.index)
    out["file"] = f.name
    out["row"] = df.index + 2                                  # the line in the file, header is line 1
    out["record_id"] = get("record_id").where(get("record_id").astype(bool), "row " + out["row"].astype(str))
    first, last = get("first_name").map(clean_name), get("last_name").map(clean_name)
    if "full_name" in col:
        split = get("full_name").map(lambda v: split_full_name(v) if isinstance(v, str) and v.strip() else (None, None))
        first = first.where(first.notna(), split.map(lambda t: t[0]))
        last = last.where(last.notna(), split.map(lambda t: t[1]))
    out["first_name"], out["last_name"] = first.fillna(""), last.fillna("")
    raw = {"email": get("email").str.lower(), "phone": get("phone"), "zip": get("zip"), "dob": get("dob")}
    out["email"] = raw["email"].where(raw["email"].map(lambda v: bool(_EMAIL.match(v))), "")
    out["phone"] = raw["phone"].map(_digits)
    out["zip"] = raw["zip"].map(_zip)
    out["dob"] = raw["dob"].map(_dob)
    year = raw["dob"].str.fullmatch(r"(19|20)\d\d")             # a vendor that only knows the year: still evidence
    out["birth_year"] = out["dob"].str[:4].where(out["dob"].astype(bool), raw["dob"].where(year, ""))
    out["street_raw"] = get("street")
    aside = {}
    for r in ("email", "phone", "zip", "dob"):
        understood = out["birth_year"] if r == "dob" else out[r]
        bad = int((raw[r].astype(bool) & ~understood.astype(bool)).sum())
        if bad:
            aside[f"{col[r]} ({ROLE_LABEL[r].lower()})"] = bad
    nobody = ~(out["last_name"].astype(bool) | out["email"].astype(bool) | out["phone"].astype(bool))
    held = df[nobody].copy()
    held.insert(0, "reason", "names nobody: no name, no valid email, no valid phone")
    held.insert(0, "row", out.loc[nobody, "row"])
    held.insert(0, "file", f.name)
    return out[~nobody], held, aside


# ------------------------------------------------------------------ match
def _matcher_shape(recs: pd.DataFrame) -> pd.DataFrame:
    from .identity.prepare import COLUMNS
    from .identity.standardize import email_user, name_group, standardize_street
    m = pd.DataFrame(index=recs.index)
    m["unique_id"] = recs["uid"]
    m["source"] = recs["file"]
    m["record_id"] = recs["record_id"]
    text = lambda v: v if isinstance(v, str) and v else None     # "" and pandas' missing values both become None
    for c in ("first_name", "last_name", "email", "phone", "zip", "dob"):
        m[c] = recs[c].astype(object).map(text)
    m["first_group"] = m["first_name"].map(lambda v: name_group(v) if isinstance(v, str) and v else None)
    m["email_user"] = m["email"].map(email_user)
    parts = recs["street_raw"].map(standardize_street)
    m["house"], m["street"], m["unit"] = zip(*parts) if len(recs) else ([], [], [])
    m["birth_year"] = recs["birth_year"].astype(object).map(text)
    return m[COLUMNS].astype(object).where(m[COLUMNS].notna(), None)


def _rules(recs: pd.DataFrame) -> pd.Series:
    """Exact rules: same email, same phone, or same first and last name at the same street address or ZIP."""
    parent = {u: u for u in recs["uid"]}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    keys = [recs["email"], recs["phone"],
            (recs["first_name"] + "|" + recs["last_name"] + "|" + recs["street_raw"].str.upper()).where(
                recs["first_name"].astype(bool) & recs["last_name"].astype(bool) & recs["street_raw"].astype(bool), ""),
            (recs["first_name"] + "|" + recs["last_name"] + "|" + recs["zip"]).where(
                recs["first_name"].astype(bool) & recs["last_name"].astype(bool) & recs["zip"].astype(bool), "")]
    for k in keys:
        first_of = {}
        for u, v in zip(recs["uid"], k):
            if not v:
                continue
            if v in first_of:
                a, b = find(u), find(first_of[v])
                if a != b:
                    parent[a] = b
            else:
                first_of[v] = u
    return recs["uid"].map(find)


def _agree(a: pd.Series, b: pd.Series) -> dict:
    """Compared as the matcher compares them: "303 Sheffield Ave Apt 4" and "303 SHEFFIELD AVENUE APT 4" are one
    address, and the first version, comparing the raw text, said they differ."""
    from .identity.standardize import standardize_street
    out = {}
    for c in ("first_name", "last_name", "email", "phone", "street_raw", "zip", "dob"):
        x, y = a[c], b[c]
        if c == "street_raw" and x and y:
            x, y = standardize_street(x), standardize_street(y)
        out["street" if c == "street_raw" else c] = "missing" if not x or not y else (
            "agree" if str(x).upper() == str(y).upper() else "differ")
    return out


# ------------------------------------------------------------------ run
def run(files: list[File], roles: list[dict[str, str]], min_records_to_learn: int = 200) -> Result:
    if not files:
        raise ValueError("no files")
    t = time.perf_counter()
    parts, held, aside = [], [], {}
    for i, (f, r) in enumerate(zip(files, roles)):
        recs, h, a = _records(f, r, i)
        parts.append(recs)
        held.append(h)
        if a:
            aside[f.name] = a
    recs = pd.concat(parts, ignore_index=True)
    recs["uid"] = [f"{i}" for i in range(len(recs))]
    seconds = {"check": time.perf_counter() - t}

    t = time.perf_counter()
    method, note, pairs = "rules", "", pd.DataFrame(columns=["unique_id_l", "unique_id_r", "match_probability"])
    if len(recs) >= min_records_to_learn and (recs["email"].astype(bool).any() or recs["phone"].astype(bool).any()):
        try:
            from .identity.match import train_and_predict
            pairs, clusters, _ = train_and_predict(_matcher_shape(recs))
            cl = dict(zip(clusters["unique_id"].astype(str), clusters["cluster_id"].astype(str)))
            recs["cluster"] = recs["uid"].map(lambda u: cl.get(u, u))
            method = "probabilistic"
            note = (f"Splink learned from these {len(recs):,} records (no labels) how much each agreement says, "
                    "then production's safety rules refused joins two different dates of birth or first names contradict.")
        except Exception as e:
            # Splink cannot train when a training block has no pairs (a file set with no phone, or no shared names).
            # Only that falls back; any other error is a bug and must not be passed off as "too little data".
            if not _too_little(e):
                raise
            note = "These files gave the matcher too little to learn from, so exact rules matched them."
    if method == "rules":
        recs["cluster"] = _rules(recs)
        note = note or ("Too few records to learn from, so exact rules matched them: the same email, the same phone, "
                        "or the same first and last name at the same street or ZIP.")
    ids = {c: f"C{i + 1:05d}" for i, c in enumerate(pd.unique(recs["cluster"]))}
    recs["person_id"] = recs["cluster"].map(ids)
    seconds["match"] = time.perf_counter() - t

    t = time.perf_counter()
    people = golden(recs)
    groups = _pairs_inside(recs, pairs)
    seconds["golden"] = time.perf_counter() - t
    held_df = pd.concat(held, ignore_index=True) if any(len(h) for h in held) else pd.DataFrame(columns=["file", "row", "reason"])
    return Result(people=people, records=recs.drop(columns=["cluster"]), held=held_df, set_aside=aside, groups=groups,
                  method=method, method_note=note, seconds=seconds, files=[f.name for f in files])


def _too_little(e: Exception) -> bool:
    text = f"{type(e).__name__}: {e}".lower()
    return any(w in text for w in ("no pairs", "zero", "empty", "insufficient", "not enough", "no comparisons",
                                    "division", "cannot estimate", "nan"))


def golden(recs: pd.DataFrame) -> pd.DataFrame:
    """One row per person: each field the value most of their records give (ties: the first file listed)."""
    rows = []
    for pid, g in recs.groupby("person_id", sort=True):
        row = {"person_id": pid}
        for c in ("first_name", "last_name", "email", "phone", "street_raw", "zip", "dob"):
            vals = [v for v in g[c] if v]
            if not vals:
                row["street" if c == "street_raw" else c] = ""
                continue
            best = Counter(vals).most_common(1)[0][0]
            row["street" if c == "street_raw" else c] = best
        row["records"] = len(g)
        row["files"] = g["file"].nunique()
        row["found_in"] = ", ".join(sorted(g["file"].unique()))
        rows.append(row)
    p = pd.DataFrame(rows)
    for c in ("first_name", "last_name"):
        p[c] = p[c].str.title()
    return p


def _pairs_inside(recs: pd.DataFrame, pairs: pd.DataFrame) -> pd.DataFrame:
    """For each person built from more than one record: each record against the first, and why they agree."""
    prob = {}
    for a, b, p in zip(pairs.get("unique_id_l", []), pairs.get("unique_id_r", []), pairs.get("match_probability", [])):
        prob[(str(a), str(b))] = prob[(str(b), str(a))] = float(p)
    out = []
    for pid, g in recs.groupby("person_id"):
        if len(g) < 2:
            continue
        head = g.iloc[0]
        for _, r in g.iloc[1:].iterrows():
            out.append({"person_id": pid, "record": f"{head['file']} · {head['record_id']}",
                        "joined_with": f"{r['file']} · {r['record_id']}",
                        "probability": prob.get((head["uid"], r["uid"])), **_agree(head, r)})
    return pd.DataFrame(out)


# ------------------------------------------------------------------ answers
QUESTIONS = {
    "How many unique customers are in my files?": "people",
    "Who appears in more than one file?": "several",
    "Which files have duplicates inside them?": "dupes",
    "Who can't be contacted?": "no_contact",
    "Where do my customers live?": "zips",
    "What was held back, and why?": "held",
}
_WORDS = {"several": ("more than one", "both", "overlap", "across", "several", "multiple files"),
          "dupes": ("duplicate", "twice", "dupes", "same person"), "no_contact": ("contact", "reach", "email", "phone"),
          "zips": ("where", "zip", "live", "location", "area"), "held": ("held", "bad", "invalid", "quarantine", "rejected", "error"),
          "people": ("how many", "unique", "customers", "count", "people")}


def route(question: str) -> str | None:
    """A typed question -> one of the exact answers, by its words. No model: what it matched is shown."""
    q = question.lower()
    for k in ("several", "dupes", "held", "no_contact", "zips", "people"):
        if any(w in q for w in _WORDS[k]):
            return k
    return None


def n_(n: int, word: str, many: str | None = None) -> str:
    """1 row, 2 rows: "1 rows held back" read as careless."""
    return f"{n:,} {word if n == 1 else (many or word + 's')}"


def answer(res: Result, kind: str) -> tuple[str, pd.DataFrame | None]:
    s, p, r = res.stats(), res.people, res.records
    if kind == "people":
        return (f"{n_(s['people'], 'unique customer')} from {n_(s['records'], 'usable row')}: {n_(s['merged'], 'row was', 'rows were')} the same "
                f"person as another row."), None
    if kind == "several":
        t = p[p["files"] > 1].sort_values("records", ascending=False)
        return f"{n_(len(t), 'customer appears', 'customers appear')} in more than one file.", t[["person_id", "first_name", "last_name", "email", "found_in", "records"]]
    if kind == "dupes":
        g = r.groupby("file")["person_id"].agg(rows="size", customers="nunique").reset_index()
        g["duplicate_rows"] = g["rows"] - g["customers"]
        return (f"{n_(int(g['duplicate_rows'].sum()), 'row repeats', 'rows repeat')} a customer already in the same file."), g
    if kind == "no_contact":
        t = p[~p["email"].astype(bool) & ~p["phone"].astype(bool)]
        return f"{n_(len(t), 'customer has', 'customers have')} no valid email and no valid phone.", t[["person_id", "first_name", "last_name", "street", "zip", "found_in"]]
    if kind == "zips":
        t = p[p["zip"].astype(bool)].groupby("zip").size().sort_values(ascending=False).head(15).rename("customers").reset_index()
        return f"Top ZIP codes of {int(p['zip'].astype(bool).sum()):,} customers with a ZIP.", t
    if kind == "held":
        if not len(res.held) and not s["set_aside"]:
            return "Nothing was held back.", None
        aside = pd.DataFrame([{"file": f, "column": c, "values not understood": n} for f, cs in res.set_aside.items() for c, n in cs.items()])
        return (f"{n_(s['held'], 'row')} held back (naming nobody); {n_(s['set_aside'], 'value')} set aside because "
                "not what their column says (the rows stay, without that value)."), aside if len(aside) else None
    raise ValueError(kind)


# ------------------------------------------------------------------ share
def share_token(res: Result) -> str:
    """A link-sized summary: counts only. No name, email, file name or value of anyone's data is in it."""
    s = res.stats()
    keep = {k: s[k] for k in ("files", "rows", "held", "people", "merged", "duplicates_inside_files", "in_several_files",
                              "reachable_email", "reachable_phone", "no_contact", "method", "seconds")}
    keep["v"] = 1
    raw = zlib.compress(json.dumps(keep, separators=(",", ":")).encode(), 9)
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def read_token(token: str) -> dict | None:
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        d = json.loads(zlib.decompress(raw, bufsize=4096)[:4096])
        allowed = {"files", "rows", "held", "people", "merged", "duplicates_inside_files", "in_several_files",
                   "reachable_email", "reachable_phone", "no_contact", "method", "seconds", "v"}
        if not isinstance(d, dict) or set(d) - allowed:
            return None
        ok = all(isinstance(d[k], (int, float)) and 0 <= d[k] < 10**9 for k in allowed - {"method", "v"} if k in d)
        return d if ok and d.get("method") in ("probabilistic", "rules") else None
    except Exception:
        return None


# ------------------------------------------------------------------ samples
def samples(run_dir: Path, n_people: int | None = None) -> tuple[list[tuple[str, bytes]], dict]:
    """Three messy files from the app's own synthetic city, as a stranger's files would arrive, and the answer key
    (file, record id) -> real person, so the result on them can be checked."""
    run_dir = Path(run_dir)
    src = run_dir / "sources"
    crm = pd.read_csv(sorted((src / "crm").glob("crm_full_*.csv"))[0], dtype=str).fillna("")
    vendor = pd.concat([pd.read_csv(p, dtype=str) for p in sorted((src / "vendor").glob("vendor_week_*.csv"))]).fillna("")
    vendor = vendor.drop_duplicates("vendor_record_id", keep="last")
    orders = pd.concat([pd.read_csv(p, dtype=str) for p in sorted((src / "store").glob("orders_*.csv"))]).fillna("")
    links = pd.read_parquet(run_dir / "truth" / "record_links.parquet")
    store_links = pd.read_parquet(run_dir / "store" / "record_links.parquet") if (run_dir / "store" / "record_links.parquet").exists() else None
    keep_o = [c for c in ("order_id", "customer_email", "first_name", "last_name", "ship_street", "ship_zip", "created_at", "total_price") if c in orders]
    orders = orders[keep_o].drop_duplicates("order_id")
    keep_v = [c for c in ("vendor_record_id", "full_name", "dob", "address_line", "zip", "phone", "email") if c in vendor]
    vendor = vendor[keep_v]
    keep_c = [c for c in ("crm_id", "first_name", "last_name", "email", "phone", "street_address", "zip") if c in crm]
    crm = crm[keep_c]
    truth = {}
    for r in links.itertuples():
        truth[("crm_export.csv" if r.source == "crm" else "vendor_append.csv", str(r.record_id))] = r.person_id
    if store_links is not None:
        for o, pid in zip(store_links["record_id"].astype(str), store_links["person_id"]):
            truth[("web_orders.csv", o)] = pid
    out = [("crm_export.csv", crm.to_csv(index=False).encode()), ("vendor_append.csv", vendor.to_csv(index=False).encode()),
           ("web_orders.csv", orders.to_csv(index=False).encode())]
    return out, truth


def check(res: Result, truth: dict) -> dict | None:
    """Pairwise precision and recall of the result against an answer key (samples only)."""
    from .identity.evaluate import pairwise_scores
    r = res.records
    real = pd.Series([truth.get((f, str(i))) for f, i in zip(r["file"], r["record_id"])], index=r.index)
    known = real.notna()
    if known.sum() < 2:
        return None
    s = pairwise_scores(r.loc[known, "person_id"], real[known])
    s["people_really"] = int(real[known].nunique())
    s["records_checked"] = int(known.sum())
    return s
