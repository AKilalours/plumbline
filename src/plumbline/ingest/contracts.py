"""Source contracts: one YAML file per source, and the rules that turn raw strings into typed values.

A source is onboarded by writing its YAML. Nothing in the engine names a specific source.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import yaml

TYPES = {"string", "name", "address", "email", "phone", "zip", "date", "loose_date", "timestamp",
         "int", "float", "flag", "enum"}


class ContractError(ValueError):
    pass


class BadValue(ValueError):
    """A single field failed its type rule. The message becomes the quarantine reason."""


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    required: bool = False
    pattern: str | None = None
    values: tuple[str, ...] = ()
    min: float | None = None
    max: float | None = None
    privacy: str | None = None   # "email" or "phone": hashed and checked against suppression
    optional: bool = False       # may be absent from a delivery; every other column must be present
    identity: str | None = None  # first_name/last_name/full_name/street/zip/dob: kept as tombstone keys on deletion


@dataclass(frozen=True)
class SourceContract:
    name: str
    kind: str
    files: str
    key: tuple[str, ...]
    columns: dict[str, Column]
    latest_by: tuple[str, ...] = ()
    aliases: dict[str, str] = field(default_factory=dict)
    role: str = "data"           # "data" or "suppression"

    @property
    def identity_cols(self) -> dict[str, str]:
        return {col.identity: n for n, col in self.columns.items() if col.identity}

    @property
    def table(self) -> str:
        return f"bronze_{self.name}"


def load_contract(path: str | Path) -> SourceContract:
    raw = yaml.safe_load(Path(path).read_text())
    try:
        cols = {}
        for name, spec in raw["columns"].items():
            t = spec["type"]
            if t not in TYPES:
                raise ContractError(f"{path}: column {name} has unknown type {t!r}")
            cols[name] = Column(name=name, type=t, required=bool(spec.get("required", False)),
                                pattern=spec.get("pattern"), values=tuple(spec.get("values", ())),
                                min=spec.get("min"), max=spec.get("max"), privacy=spec.get("privacy"),
                                optional=bool(spec.get("optional", False)), identity=spec.get("identity"))
            if cols[name].identity not in (None, "first_name", "last_name", "full_name", "street", "zip", "dob"):
                raise ContractError(f"{path}: column {name} has unknown identity role {cols[name].identity!r}")
        c = SourceContract(name=raw["name"], kind=raw["kind"], files=raw["files"], key=tuple(raw["key"]),
                           columns=cols, latest_by=tuple(raw.get("latest_by") or ()),
                           aliases=dict(raw.get("aliases") or {}), role=raw.get("role", "data"))
    except KeyError as e:
        raise ContractError(f"{path}: missing field {e}") from None
    missing = [k for k in c.key + c.latest_by if k not in c.columns]
    if missing:
        raise ContractError(f"{path}: key or latest_by names unknown columns {missing}")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", c.name):
        raise ContractError(f"{path}: source name must be lowercase snake_case")
    return c


def load_contracts(directory: str | Path) -> dict[str, SourceContract]:
    contracts = [load_contract(p) for p in sorted(Path(directory).glob("*.yaml"))]
    return {c.name: c for c in contracts}


# ------------------------------------------------------------------ normalization
_WS = re.compile(r"\s+")


def email_hash(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


def phone_hash(digits: str) -> str:
    return hashlib.sha256(digits.encode()).hexdigest()


def normalize(col: Column, raw: str):
    """Return a typed value, None for empty, or raise BadValue with a reason code."""
    s = _WS.sub(" ", raw).strip() if raw is not None else ""
    if s == "":
        if col.required:
            raise BadValue(f"missing:{col.name}")
        return None
    t = col.type
    if t in ("string", "name"):
        v = s
    elif t == "address":
        v = s.upper()
    elif t == "email":
        v = s.lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z0-9]+", v):
            raise BadValue(f"bad_email:{col.name}")
    elif t == "phone":
        d = re.sub(r"\D", "", s)
        if len(d) == 11 and d.startswith("1"):
            d = d[1:]
        if len(d) != 10:
            raise BadValue(f"bad_phone:{col.name}")
        v = d
    elif t == "zip":
        if not re.fullmatch(r"\d{5}", s):
            raise BadValue(f"bad_zip:{col.name}")
        v = s
    elif t == "date":
        try:
            v = date.fromisoformat(s)
        except ValueError:
            raise BadValue(f"bad_date:{col.name}") from None
    elif t == "loose_date":
        # Vendors send ISO, MM/DD/YYYY or a bare year. Keep the precision they gave.
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            v = s
        elif m := re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", s):
            v = f"{m.group(3)}-{m.group(1)}-{m.group(2)}"
        elif re.fullmatch(r"\d{4}", s):
            v = s
        else:
            raise BadValue(f"bad_date:{col.name}")
        if len(v) == 10:
            try:
                date.fromisoformat(v)
            except ValueError:
                raise BadValue(f"bad_date:{col.name}") from None
    elif t == "timestamp":
        try:
            v = datetime.fromisoformat(s)
        except ValueError:
            raise BadValue(f"bad_timestamp:{col.name}") from None
    elif t in ("int", "float"):
        try:
            v = int(s) if t == "int" else float(s)
        except ValueError:
            raise BadValue(f"bad_number:{col.name}") from None
        if (col.min is not None and v < col.min) or (col.max is not None and v > col.max):
            raise BadValue(f"out_of_range:{col.name}")
    elif t == "flag":
        u = s.upper()
        if u not in ("Y", "N"):
            raise BadValue(f"bad_flag:{col.name}")
        v = u == "Y"
    elif t == "enum":
        if s not in col.values:
            raise BadValue(f"bad_enum:{col.name}")
        v = s
    else:  # pragma: no cover - load_contract rejects unknown types
        raise BadValue(f"unknown_type:{col.name}")
    if col.pattern and isinstance(v, str) and not re.fullmatch(col.pattern, v):
        raise BadValue(f"bad_pattern:{col.name}")
    return v
