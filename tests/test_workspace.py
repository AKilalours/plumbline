"""Anyone's customer files through the real pipeline. Each test is one promise the "Try your files" page makes."""
from __future__ import annotations

import base64
import json
import zlib
from pathlib import Path

import pytest

from plumbline import workspace as W

ROOT = Path(__file__).resolve().parents[1]


def _f(name, text, enc="utf-8"):
    return W.read(name, text.encode(enc))


def test_columns_are_found_from_headers_and_values_in_several_styles():
    a = _f("crm.csv", "first_name,last_name,email,phone,street_address,zip\nAnn,Lee,ann@x.test,555-111-2222,1 Main St,11201\n")
    b = _f("v.csv", "vendor_record_id,full_name,dob,address_line,zip\nV1,Ann Lee,04/24/1971,1 MAIN STREET,11201\n")
    c = _f("fr.csv", "Nom;Prénom;Courriel;Téléphone\nLee;Ann;ann@x.test;555 111 2222\n", "latin-1")
    ra = W.detect(a)
    assert [ra[k] for k in ("first_name", "last_name", "email", "phone", "street_address", "zip")] == \
        ["first_name", "last_name", "email", "phone", "street", "zip"]
    assert [W.detect(b)[k] for k in ("full_name", "dob", "address_line")] == ["full_name", "dob", "street"]
    assert W.detect(c) == {"Nom": "last_name", "Prénom": "first_name", "Courriel": "email", "Téléphone": "phone"}


def test_the_same_person_across_files_and_inside_one_becomes_one_customer():
    a = _f("crm.csv", "first,last,email,phone\nAnn,Lee,ann@x.test,555-111-2222\nAnn,Lee,ANN@X.TEST,\nBo,Kim,bo@y.test,\n")
    b = _f("shop.csv", "name,mobile,zip\nAnn Lee,(555) 111-2222,11201\nCy Diaz,555-999-0000,10001\n")
    res = W.run([a, b], [W.detect(a), W.detect(b)])
    s = res.stats()
    assert s["people"] == 3 and s["in_several_files"] == 1 and s["duplicates_inside_files"] == 1 and s["method"] == "rules"
    ann = res.people[res.people["last_name"] == "Lee"].iloc[0]
    assert ann["records"] == 3 and ann["email"] == "ann@x.test" and ann["phone"] == "5551112222" and ann["zip"] == "11201"


def test_a_row_that_names_nobody_is_held_back_and_a_bad_value_is_set_aside_not_the_row():
    a = _f("x.csv", "name,email,zip\nAnn Lee,not-an-email,ABCDE\nzz,,\n,nobody,\n")
    res = W.run([a], [W.detect(a)])
    assert len(res.held) == 1 and res.held.iloc[0]["row"] == 4
    assert sum(res.set_aside["x.csv"].values()) == 3            # the two bad emails and the bad ZIP


def test_a_year_alone_counts_as_a_birth_year_not_a_bad_value():
    a = _f("v.csv", "full_name,dob\nAnn Lee,1978\nBo Kim,04/24/1971\n")
    recs, held, aside = W._records(a, W.detect(a), 0)
    assert list(recs["birth_year"]) == ["1978", "1971"] and not aside


def test_files_that_are_not_tables_are_refused_with_a_reason():
    for bad in (b"", b"one line only", b"a\n1\n"):
        with pytest.raises(ValueError):
            W.read("x.csv", bad)


def test_a_big_file_is_cut_at_the_limit_and_says_so():
    f = W.read("big.csv", ("a,b\n" + "1,2\n" * 30).encode(), max_rows=10)
    assert len(f.df) == 10 and f.truncated and f.rows_in_file == 30


def test_the_share_link_carries_counts_only_and_refuses_anything_else():
    a = _f("secret_clients.csv", "name,email\nAnn Lee,ann@private.test\nAnn Lee,ann@private.test\n")
    res = W.run([a], [W.detect(a)])
    back = W.read_token(W.share_token(res))
    assert back["people"] == 1 and back["rows"] == 2
    blob = json.dumps(back)
    assert "Ann" not in blob and "private" not in blob and "secret" not in blob
    forged = base64.urlsafe_b64encode(zlib.compress(json.dumps({"people": 1, "name": "x", "method": "rules"}).encode())).decode()
    assert W.read_token(forged) is None and W.read_token("garbage") is None


def test_a_matcher_error_that_is_not_too_little_data_is_raised_not_hidden(monkeypatch):
    """The first version caught every error and said the files gave too little to learn from: a pandas bug (a
    missing value read back as a number) was reported to the visitor as a fact about their data."""
    import plumbline.identity.match as M

    def broken(*a, **k):
        raise TypeError("'float' object is not subscriptable")
    monkeypatch.setattr(M, "train_and_predict", broken)
    rows = "\n".join(f"P{i},Name{i},Last{i % 50},p{i}@x.test,555{i:07d}" for i in range(300))
    a = _f("x.csv", "id,first_name,last_name,email,phone\n" + rows)
    with pytest.raises(TypeError):
        W.run([a], [W.detect(a)])


def test_a_blank_value_reaches_the_matcher_as_none_not_a_number():
    """pandas read a blank back as NaN, and the matcher's input crashed on it (the bug the test above hid)."""
    a = _f("x.csv", "first_name,last_name,email,dob\nAnn,Lee,ann@x.test,\nBo,Kim,,1971-04-24\n")
    recs, _, _ = W._records(a, W.detect(a), 0)
    recs["uid"] = ["0", "1"]
    m = W._matcher_shape(recs)
    assert m.loc[0, "dob"] is None and m.loc[0, "birth_year"] is None and m.loc[1, "email"] is None
    assert m.loc[1, "birth_year"] == "1971"


def test_one_address_written_two_ways_is_shown_as_agreeing():
    a = _f("a.csv", "first_name,last_name,email,street\nEd,Pearson,ed@x.test,303 Sheffield Ave Apt 4\n")
    b = _f("b.csv", "first_name,last_name,email,street\nEd,Pearson,ed@x.test,303 SHEFFIELD AVENUE APT 4\n")
    res = W.run([a, b], [W.detect(a), W.detect(b)])
    assert res.groups.iloc[0]["street"] == "agree"
