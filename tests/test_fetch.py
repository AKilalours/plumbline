"""Regression tests for the reference download, written after the first real run failed."""
from __future__ import annotations

import pytest

from plumbline import reference_fetch as rf


def test_acs_url_encodes_spaces_as_percent20():
    url = rf.acs_url(2023)
    assert "zip%20code%20tabulation%20area" in url
    assert "+" not in url and "key=" not in url


def test_acs_url_adds_key_only_when_given():
    assert "key=abc" in rf.acs_url(2023, "abc")


def test_non_json_census_reply_is_shown_not_swallowed():
    """The first run died with a bare JSONDecodeError; the server's own words must surface."""
    with pytest.raises(rf.FetchError, match="Invalid Key"):
        rf.parse_acs(b"<html><body>Invalid Key</body></html>", {"11211"})
    with pytest.raises(rf.FetchError, match="empty response"):
        rf.parse_acs(b"", {"11211"})


def test_parse_acs_keeps_only_requested_zips():
    body = (b'[["NAME","B19013_001E","zip code tabulation area"],'
            b'["ZCTA5 11211","98000","11211"],["ZCTA5 90210","200000","90210"]]')
    assert rf.parse_acs(body, {"11211"}) == [{"zcta": "11211", "median_income": "98000"}]


def test_local_pluto_drops_owner_columns(tmp_path):
    p = tmp_path / "pluto.csv"
    p.write_text("BBL,Address,ZipCode,Borough,UnitsRes,YearBuilt,BldgClass,OwnerName\n"
                 "1,1 A ST,11211,BK,2,1920,A1,SOMEONE REAL\n")
    rows = rf.read_local_pluto(p)
    assert rows == [{"bbl": "1", "address": "1 A ST", "zipcode": "11211", "borough": "BK",
                     "unitsres": "2", "yearbuilt": "1920", "bldgclass": "A1"}]


def test_atomic_write_leaves_no_partial_file(tmp_path):
    target = tmp_path / "x.csv"
    with pytest.raises(ValueError):
        rf.write_csv_atomic(target, ["a"], [{"a": 1}, {"b": 2}])  # second row has an unknown field
    assert not target.exists()


def test_failed_acs_does_not_mark_directory_ready_and_reuses_pluto(tmp_path, monkeypatch):
    """A failure after PLUTO must not write REFERENCE_SOURCE, and the rerun must not re-download PLUTO."""
    out = tmp_path / "ref"
    calls = {"pluto": 0}

    def fake_get(url):
        if "data.cityofnewyork.us" in url:
            calls["pluto"] += 1
            return b"bbl,address,zipcode,borough,unitsres,yearbuilt,bldgclass\n1,1 A ST,11211,BK,2,1920,A1\n"
        return b"<html>maintenance</html>"

    monkeypatch.setenv("CENSUS_API_KEY", "test-key")
    monkeypatch.setattr(rf, "_get", fake_get)
    assert rf.main(["--out", str(out)]) == 1
    assert (out / "pluto_lots.csv").exists() and not (out / "REFERENCE_SOURCE").exists()

    monkeypatch.setattr(rf, "_get", lambda url: (b'[["NAME","B19013_001E","zip code tabulation area"],'
                                                 b'["ZCTA5 11211","98000","11211"]]'))
    assert rf.main(["--out", str(out)]) == 0
    assert calls["pluto"] == 1
    assert (out / "REFERENCE_SOURCE").read_text().startswith("real")


def test_missing_census_key_fails_early_with_instructions(tmp_path, monkeypatch, capsys):
    """Regression: Census now answers "Missing Key"; say so before calling it."""
    out = tmp_path / "ref"
    out.mkdir()
    (out / "pluto_lots.csv").write_text("bbl,address,zipcode,borough,unitsres,yearbuilt,bldgclass\n1,1 A ST,11211,BK,2,1920,A1\n")
    monkeypatch.delenv("CENSUS_API_KEY", raising=False)
    monkeypatch.setattr(rf, "_get", lambda url: pytest.fail("must not call Census without a key"))
    assert rf.main(["--out", str(out)]) == 1
    assert "CENSUS_API_KEY is not set" in capsys.readouterr().err
