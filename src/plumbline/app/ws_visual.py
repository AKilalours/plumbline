"""The merge stage on "Try your files": every row a visitor sent, as a dot in its file's colour, flying to the customer
it became. Built from the real result of their files (workspace.run), nothing staged.

The browser gets one pair per row (which file, which customer), the customer names, and full evidence for the
customers most worth a look: joined across files despite a difference (a typo, another address) first.
"""
from __future__ import annotations

FIELDS = ("first_name", "last_name", "email", "phone", "street", "zip", "dob")


def merge_data(res, check: dict | None = None, *, max_detail: int = 240, colors=None) -> dict:
    r, p, g = res.records, res.people, res.groups
    colors = colors or ["#2941B9", "#E5B74D", "#1E8A5A", "#C2412D", "#7A5AF8"]
    order = {pid: i for i, pid in enumerate(p["person_id"])}
    fidx = {n: i for i, n in enumerate(res.files)}
    recs = [[fidx[f], order[pid]] for f, pid in zip(r["file"], r["person_id"])]
    people = [f"{a} {b}".strip().title() or "(no name)" for a, b in zip(p["first_name"], p["last_name"])]

    # who is worth opening: joined across files, and joined despite a field that differs
    differ = (g[[c for c in FIELDS if c in g]].eq("differ").sum(axis=1).groupby(g["person_id"]).sum()
              if len(g) else None)
    many = p[p["records"] > 1].copy()
    many["d"] = many["person_id"].map(differ).fillna(0) if differ is not None else 0
    many = many.sort_values(["d", "files", "records"], ascending=[False, False, False]).head(max_detail)
    detail = {}
    by_pid = r.groupby("person_id")
    for pid in many["person_id"]:
        rows = by_pid.get_group(pid)
        pairs = g[g["person_id"] == pid] if len(g) else []
        detail[str(order[pid])] = {
            "rows": [{"file": fidx[x["file"]], "id": str(x["record_id"]), "first": x.get("first_name", ""), "last": x.get("last_name", ""),
                      "email": x.get("email", ""), "phone": x.get("phone", ""), "street": x.get("street_raw", ""), "zip": x.get("zip", "")}
                     for x in rows.to_dict("records")],
            "pairs": [{"a": x["record"], "b": x["joined_with"],
                       "p": None if x.get("probability") is None or x.get("probability") != x.get("probability") else round(float(x["probability"]), 4),
                       "f": {c: x[c] for c in FIELDS if c in x and x[c]}} for x in (pairs.to_dict("records") if len(pairs) else [])]}
    s = res.stats()
    held = res.held
    return {"files": [{"name": n, "color": colors[i % len(colors)], "rows": int((r["file"] == n).sum())} for i, n in enumerate(res.files)],
            "recs": recs, "people": people, "detail": detail,
            "held": {"n": int(len(held)), "reasons": held["reason"].value_counts().head(4).to_dict() if len(held) else {}},
            "stats": {k: s[k] for k in ("rows", "people", "merged", "in_several_files", "duplicates_inside_files", "seconds", "method")},
            "check": {k: check[k] for k in ("precision", "recall", "people_resolved_exactly", "people_really")} if check else None,
            "spotlight": [int(k) for k in list(detail)[:6]]}
