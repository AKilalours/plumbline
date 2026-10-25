"""Download the two real public reference tables Plumbline needs.

    python scripts/fetch_reference.py --out data/reference

1. NYC PLUTO tax lots (NYC Open Data, Socrata dataset 64uk-42ks). Only the columns below
   are requested; the owner-name field is never downloaded. A manually downloaded PLUTO
   CSV can be passed with --pluto-csv; owner columns are dropped before anything is written.
2. Census ACS 5-year median household income (table B19013) by ZIP code tabulation area.
   Requires a free Census API key (https://api.census.gov/data/key_signup.html) in the
   CENSUS_API_KEY environment variable; the API answers "Missing Key" without one.

Resumable: each table is written to a temporary file and renamed only when complete, so a
file that exists is a finished download. A rerun reuses it unless --refresh is passed.
REFERENCE_SOURCE is written last, so the generator never sees a half-fetched directory.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PLUTO_DATASET = "64uk-42ks"
PLUTO_COLUMNS = ["bbl", "address", "zipcode", "borough", "unitsres", "yearbuilt", "bldgclass"]
PAGE = 50_000
USER_AGENT = "plumbline-reference-fetch/0.2 (portfolio project)"


class FetchError(RuntimeError):
    pass


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/csv"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        body = e.read()[:400].decode(errors="replace")
        raise FetchError(f"HTTP {e.code} from {url.split('?')[0]}: {body}") from None


def _query(params: dict) -> str:
    # quote, not quote_plus: the Census API documents spaces as %20 ("zip%20code%20tabulation%20area").
    return urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


def pluto_page_url(offset: int) -> str:
    return (f"https://data.cityofnewyork.us/resource/{PLUTO_DATASET}.csv?"
            + _query({"$select": ",".join(PLUTO_COLUMNS), "$where": "unitsres > 0",
                      "$order": "bbl", "$limit": PAGE, "$offset": offset}))


def acs_url(year: int, key: str | None = None) -> str:
    params = {"get": "NAME,B19013_001E", "for": "zip code tabulation area:*"}
    if key:
        params["key"] = key
    return f"https://api.census.gov/data/{year}/acs/acs5?{_query(params)}"


def parse_acs(body: bytes, zips: set[str]) -> list[dict]:
    """Census returns a JSON array of rows. Anything else is an error page: show it, don't guess."""
    text = body.decode(errors="replace")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        snippet = " ".join(text.split())[:400] or "(empty response)"
        raise FetchError(
            "Census API did not return JSON. It said: " + snippet +
            "\nIf it mentions a key or a limit, get a free key at "
            "https://api.census.gov/data/key_signup.html and set CENSUS_API_KEY.") from None
    header, rows = data[0], data[1:]
    zi, vi = header.index("zip code tabulation area"), header.index("B19013_001E")
    return [{"zcta": r[zi], "median_income": r[vi]} for r in rows if r[zi] in zips]


def fetch_pluto() -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        page = list(csv.DictReader(io.StringIO(_get(pluto_page_url(offset)).decode())))
        rows.extend(page)
        print(f"  pluto: {len(rows):,} lots", file=sys.stderr)
        if len(page) < PAGE:
            return rows
        offset += PAGE


def read_local_pluto(path: str | Path) -> list[dict]:
    with open(path, newline="") as f:
        out = []
        for r in csv.DictReader(f):
            low = {k.strip().lower(): v for k, v in r.items() if not k.strip().lower().startswith("owner")}
            out.append({c: low.get(c, "") for c in PLUTO_COLUMNS})
        return out


def write_csv_atomic(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    tmp = path.with_suffix(path.suffix + ".partial")
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Download PLUTO tax lots and Census incomes.")
    ap.add_argument("--out", default="data/reference")
    ap.add_argument("--pluto-csv", help="use a manually downloaded PLUTO CSV instead of the API")
    ap.add_argument("--acs-year", type=int, default=2023)
    ap.add_argument("--refresh", action="store_true", help="re-download tables that already exist")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    marker, pluto_path, acs_path = out / "REFERENCE_SOURCE", out / "pluto_lots.csv", out / "acs_income.csv"
    if marker.exists():
        marker.unlink()  # the directory is incomplete until this run finishes

    try:
        if pluto_path.exists() and not a.refresh and not a.pluto_csv:
            print(f"  pluto: reusing {pluto_path} (pass --refresh to download again)", file=sys.stderr)
            with open(pluto_path, newline="") as f:
                lots = list(csv.DictReader(f))
            pluto_desc = "reused"
        else:
            lots = read_local_pluto(a.pluto_csv) if a.pluto_csv else fetch_pluto()
            if not lots or any(k.startswith("owner") for k in lots[0]):
                raise FetchError("no lots, or owner columns present")
            write_csv_atomic(pluto_path, PLUTO_COLUMNS, lots)
            pluto_desc = f"local:{a.pluto_csv}" if a.pluto_csv else f"nycopendata:{PLUTO_DATASET}"

        key = os.environ.get("CENSUS_API_KEY", "").strip()
        if not key:
            raise FetchError("CENSUS_API_KEY is not set. Get a free key at "
                             "https://api.census.gov/data/key_signup.html, then: export CENSUS_API_KEY=yourkey")
        zips = {str(r.get("zipcode", ""))[:5] for r in lots if r.get("zipcode")}
        acs = parse_acs(_get(acs_url(a.acs_year, key)), zips)
        if not acs:
            raise FetchError("Census returned no incomes for the PLUTO ZIP codes")
        write_csv_atomic(acs_path, ["zcta", "median_income"], acs)
    except (FetchError, urllib.error.URLError) as e:
        print(f"fetch failed: {e}", file=sys.stderr)
        return 1

    marker.write_text(
        "real\n"
        f"fetched_at={datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
        f"pluto={pluto_desc} rows={len(lots)}\n"
        f"acs=acs5/{a.acs_year}/B19013 zctas={len(acs)}\n"
        f"pluto_lots.csv sha256={_sha(pluto_path)}\n"
        f"acs_income.csv sha256={_sha(acs_path)}\n")
    print(f"wrote {len(lots):,} lots and {len(acs)} ZIP incomes to {out}", file=sys.stderr)
    return 0
