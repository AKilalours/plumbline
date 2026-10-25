"""Turn scored pairs into persons, strongest evidence first, with cannot-link rules.

Plain connected components chains: A~B and B~C puts A and C together even if A and C clearly are
two people (B is a record with no first name; A and C are spouses at one address). Here clusters
merge in order of match probability, and a merge is refused if it would put two records in one
person that cannot be the same person:
  - both have a first name, and the names are neither the same, nicknames of each other, nor within
    typo distance (Jaro-Winkler >= 0.88), or
  - both have a birth year and the years differ.
This costs recall where a nickname is missing from the list (Tina for Christina) and buys
precision inside households, where address, last name and zip all agree for different people.
"""
from __future__ import annotations

from functools import lru_cache

import pandas as pd

from .standardize import could_be_same_first_name


@lru_cache(maxsize=None)
def jaro_winkler(a: str, b: str) -> float:
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    if not la or not lb:
        return 0.0
    w = max(la, lb) // 2 - 1
    ma, mb = [False] * la, [False] * lb
    m = 0
    for i, ch in enumerate(a):
        for j in range(max(0, i - w), min(lb, i + w + 1)):
            if not mb[j] and b[j] == ch:
                ma[i] = mb[j] = True
                m += 1
                break
    if not m:
        return 0.0
    t, k = 0, 0
    for i in range(la):
        if ma[i]:
            while not mb[k]:
                k += 1
            t += a[i] != b[k]
            k += 1
    j = (m / la + m / lb + (m - t / 2) / m) / 3
    p = 0
    for x, y in zip(a[:4], b[:4]):
        if x != y:
            break
        p += 1
    return j + p * 0.1 * (1 - j)


@lru_cache(maxsize=None)
def first_names_compatible(a: str, b: str) -> bool:
    return a == b or could_be_same_first_name(a, b) or jaro_winkler(a, b) >= 0.88


def constrained_clusters(records: pd.DataFrame, pairs: pd.DataFrame, threshold: float) -> pd.DataFrame:
    ids = records["unique_id"].tolist()
    idx = {u: i for i, u in enumerate(ids)}
    parent = list(range(len(ids)))
    # pd.notna, not truthiness: under pandas 3 a missing string is NaN, and NaN is truthy.
    firsts = [{f} if pd.notna(f) and f else set() for f in records["first_name"]]
    years = [{y} if pd.notna(y) and y else set() for y in records["birth_year"]]

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    # Merge order decides outcomes once merges can be refused, so it must be a total order. Pairs
    # with equal probability arrive in a different order on every run (parallel scans), hence the
    # tie-break on the ids and a rounded probability (last-digit float noise must not reorder).
    strong = pairs[pairs["match_probability"] >= threshold].assign(_p=lambda d: d.match_probability.round(9))
    strong = strong.sort_values(["_p", "unique_id_l", "unique_id_r"], ascending=[False, True, True], kind="mergesort")
    refused = 0
    for l, r in zip(strong["unique_id_l"], strong["unique_id_r"]):
        a, b = find(idx[l]), find(idx[r])
        if a == b:
            continue
        if (years[a] and years[b] and years[a] != years[b]) or \
           any(not first_names_compatible(x, y) for x in firsts[a] for y in firsts[b]):
            refused += 1
            continue
        a, b = min(a, b), max(a, b)
        parent[b] = a
        firsts[a] |= firsts[b]
        years[a] |= years[b]
    # Name each cluster by its smallest member id, which does not depend on input row order.
    roots = pd.Series([find(i) for i in range(len(ids))])
    name = pd.Series(ids).groupby(roots).transform("min")
    out = pd.DataFrame({"unique_id": ids, "cluster_id": name.to_numpy()})
    out.attrs["refused_merges"] = refused
    return out
