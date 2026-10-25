"""Name, job and employer pools.

Only Faker's static word lists are used, never Faker's own random generator, so the
output depends on our seed alone. The Faker version is still recorded in the
manifest because a new release can change those lists.
"""
from __future__ import annotations

import re

import numpy as np
from faker.providers.job import Provider as _JobProvider
from faker.providers.person.en_US import Provider as _PersonProvider


def _weighted(mapping) -> tuple[np.ndarray, np.ndarray]:
    keys = np.array(list(mapping.keys()), dtype=object)
    w = np.array(list(mapping.values()), dtype=float)
    return keys, w / w.sum()


FEMALE_FIRST = _weighted(_PersonProvider.first_names_female)
MALE_FIRST = _weighted(_PersonProvider.first_names_male)
LAST = _weighted(_PersonProvider.last_names)
JOBS = np.array(sorted(set(_JobProvider.jobs)), dtype=object)

EMPLOYER_SUFFIXES = np.array(
    ["Group", "Partners", "Labs", "Holdings", "Systems", "Studio", "Logistics",
     "Health", "Capital", "Foods", "Media", "Works", "Analytics", "Supply"], dtype=object)

PERSONAL_DOMAINS = np.array(["kestrelmail.test", "inbox.test", "postbox.test", "webmail.test"], dtype=object)

# Common US nicknames. A source that records "Bill" for William is a classic matching trap.
NICKNAMES = {
    "William": "Bill", "Robert": "Bob", "Richard": "Rick", "James": "Jim", "John": "Jack",
    "Michael": "Mike", "Joseph": "Joe", "Thomas": "Tom", "Charles": "Chuck", "Christopher": "Chris",
    "Daniel": "Dan", "Matthew": "Matt", "Anthony": "Tony", "Andrew": "Andy", "Joshua": "Josh",
    "Nicholas": "Nick", "Benjamin": "Ben", "Samuel": "Sam", "Alexander": "Alex", "Jonathan": "Jon",
    "Elizabeth": "Liz", "Jennifer": "Jen", "Katherine": "Kate", "Margaret": "Peggy", "Patricia": "Pat",
    "Susan": "Sue", "Deborah": "Deb", "Rebecca": "Becky", "Kimberly": "Kim", "Jessica": "Jess",
    "Victoria": "Vicky", "Abigail": "Abby", "Samantha": "Sam", "Alexandra": "Alex", "Christina": "Tina",
    "Stephanie": "Steph", "Jacqueline": "Jackie", "Theresa": "Terry", "Catherine": "Cathy", "Pamela": "Pam",
}


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def sample_weighted(rng: np.random.Generator, pool: tuple[np.ndarray, np.ndarray], n: int) -> np.ndarray:
    keys, p = pool
    return keys[rng.choice(len(keys), size=n, p=p)]


def employer_pool(rng: np.random.Generator, size: int) -> np.ndarray:
    lasts = sample_weighted(rng, LAST, size * 2)
    sufs = EMPLOYER_SUFFIXES[rng.integers(0, len(EMPLOYER_SUFFIXES), size=size * 2)]
    seen: dict[str, None] = {}
    for a, b in zip(lasts, sufs):
        name = f"{a} {b}"
        if name not in seen:
            seen[name] = None
        if len(seen) == size:
            break
    return np.array(list(seen.keys()), dtype=object)
