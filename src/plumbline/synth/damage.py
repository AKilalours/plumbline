"""How a source gets a person wrong. Every change is written to a damage log.

The log is part of the answer key: it says which field of which record was damaged
and how, so a matcher's misses can be traced to the kind of damage that caused them.
"""
from __future__ import annotations

import numpy as np

from .names import NICKNAMES

ABBREVIATIONS = {
    " STREET": " ST", " AVENUE": " AVE", " BOULEVARD": " BLVD", " PLACE": " PL",
    " ROAD": " RD", " PARKWAY": " PKWY", " EAST ": " E ", " WEST ": " W ",
    " NORTH ": " N ", " SOUTH ": " S ",
}
LETTERS = "abcdefghijklmnopqrstuvwxyz"


class DamageLog:
    def __init__(self, source: str):
        self.source = source
        self.rows: list[tuple[str, str, str, str]] = []

    def add(self, record_id: str, field: str, kind: str) -> None:
        self.rows.append((self.source, record_id, field, kind))


def typo(s: str, rng: np.random.Generator) -> str:
    """One keyboard-style error, never at position 0 so simple blocking keys survive."""
    if not s or len(s) < 3:
        return s
    i = int(rng.integers(1, len(s)))
    op = int(rng.integers(0, 4))
    upper = s[i].isupper()
    c = LETTERS[int(rng.integers(0, 26))]
    c = c.upper() if upper else c
    if op == 0 and i < len(s) - 1:          # swap adjacent
        out = s[:i] + s[i + 1] + s[i] + s[i + 2:]
    elif op == 1:                            # delete
        out = s[:i] + s[i + 1:]
    elif op == 2:                            # replace
        out = s[:i] + c + s[i + 1:]
    else:                                    # insert
        out = s[:i] + c + s[i:]
    return out if out != s else s[:i] + s[i + 1:]


def maybe_nickname(first: str, rate: float, rng: np.random.Generator) -> tuple[str, bool]:
    nick = NICKNAMES.get(first)
    if nick and rng.random() < rate:
        return nick, True
    return first, False


def abbreviate(street: str) -> str:
    out = f" {street} "
    for long, short in ABBREVIATIONS.items():
        out = out.replace(long + " ", short + " ")
    return out.strip()


def phone_format(digits: str, style: int) -> str:
    a, b, c = digits[:3], digits[3:6], digits[6:]
    return [f"({a}) {b}-{c}", f"{a}-{b}-{c}", f"{a}{b}{c}", f"+1 {a} {b} {c}"][style]
