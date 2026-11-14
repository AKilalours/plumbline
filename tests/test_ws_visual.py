"""The merge stage on Try your files: built from the real run of the files, every row accounted for."""
from plumbline import workspace as W
from plumbline.app.ws_visual import merge_data

CRM = b"""first_name,last_name,email,phone,zip
Jason,Gallegos,gallegos.jason@kestrelmail.test,5555491898,10453
Sherri,Wheeler,sherri.wheeler@postbox.test,5553391835,10021
Ana,Ruiz,ana.ruiz@postbox.test,5551112222,11211
"""
WEB = b"""name,email,zip
jaon gallegos,gallegos.jason@kestrelmail.test,10453
Sherri Wheeler,sherri.wheeler@postbox.test,10021
Tom Baker,tom.baker@postbox.test,10003
"""


def _run():
    fs = [W.read("crm.csv", CRM), W.read("web.csv", WEB)]
    return W.run(fs, [W.detect(f) for f in fs])


def test_every_row_sent_is_one_dot_and_lands_on_its_customer():
    res = _run()
    d = merge_data(res)
    assert len(d["recs"]) == len(res.records) == 6
    assert [f["rows"] for f in d["files"]] == [3, 3]
    assert len(d["people"]) == res.stats()["people"] == 4                 # Jason and Sherri joined across the files
    landed = {}
    for f, p in d["recs"]:
        landed.setdefault(p, set()).add(f)
    assert sorted(len(v) for v in landed.values()) == [1, 1, 2, 2]


def test_customers_joined_despite_a_typo_come_first_with_their_evidence():
    d = merge_data(_run())
    first = d["detail"][str(d["spotlight"][0])]
    assert {r["first"].lower() for r in first["rows"]} == {"jason", "jaon"}
    assert first["pairs"] and first["pairs"][0]["f"].get("first_name") == "differ"
