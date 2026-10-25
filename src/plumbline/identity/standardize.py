"""Put every source's names, addresses and contact fields into one comparable shape.

These tables are written independently of the synthetic generator on purpose. If the matcher used
the generator's own nickname and abbreviation lists, it would know exactly how the data was damaged
and its scores would be inflated. Street suffixes follow the USPS standard (Publication 28,
appendix C); nicknames are a hand-written list of common US English pairs.
"""
from __future__ import annotations

import re

USPS_SUFFIX = {
    "STREET": "ST", "STR": "ST", "AVENUE": "AVE", "AV": "AVE", "AVEN": "AVE", "BOULEVARD": "BLVD",
    "BOUL": "BLVD", "PLACE": "PL", "ROAD": "RD", "PARKWAY": "PKWY", "PKY": "PKWY", "DRIVE": "DR",
    "LANE": "LN", "COURT": "CT", "TERRACE": "TER", "HIGHWAY": "HWY", "EXPRESSWAY": "EXPY",
    "SQUARE": "SQ", "TURNPIKE": "TPKE", "CIRCLE": "CIR", "PLAZA": "PLZ", "BROADWAY": "BROADWAY",
    "CONCOURSE": "CONC", "LOOP": "LOOP", "WALK": "WALK",
}
DIRECTIONAL = {"EAST": "E", "WEST": "W", "NORTH": "N", "SOUTH": "S"}
UNIT_WORDS = r"(?:APT|APARTMENT|UNIT|STE|SUITE|FL|FLOOR|RM|ROOM|#)"

# nickname -> formal name. Several formal names share some nicknames (Chris, Alex, Sam); those map
# to one group key, which is enough for "could be the same first name".
_NICK_GROUPS = {
    "william": "bill billy will willie liam", "robert": "bob bobby rob robbie bert",
    "richard": "rick ricky rich dick", "james": "jim jimmy jamie", "john": "jack johnny jon",
    "michael": "mike mikey mick", "joseph": "joe joey", "thomas": "tom tommy",
    "charles": "chuck charlie chaz", "christopher": "chris kit topher christina christine kristen kristina",
    "daniel": "dan danny", "matthew": "matt matty", "anthony": "tony", "andrew": "andy drew",
    "joshua": "josh", "nicholas": "nick nicky", "jonathan": "jon jonny", "benjamin": "ben benny benji",
    "samuel": "sam sammy samantha", "alexander": "alex al alexandra alexis lex", "edward": "ed eddie ted ned",
    "timothy": "tim timmy", "steven": "steve stephen stevie", "kenneth": "ken kenny",
    "ronald": "ron ronnie", "donald": "don donnie", "gregory": "greg", "patrick": "pat patty patricia trish tricia",
    "jeffrey": "jeff", "zachary": "zach zack", "nathan": "nate nathaniel", "frederick": "fred freddie",
    "lawrence": "larry", "raymond": "ray", "gerald": "jerry gerry", "peter": "pete",
    "douglas": "doug", "eugene": "gene", "harold": "harry hal", "henry": "hank", "leonard": "leo len lenny",
    "elizabeth": "liz beth betty eliza lizzie libby bess", "margaret": "maggie meg peggy marge",
    "katherine": "kate katie kathy cathy kat catherine kathryn kathleen", "jennifer": "jen jenny",
    "jessica": "jess jessie", "rebecca": "becky becca", "deborah": "deb debbie debra",
    "susan": "sue susie suzanne", "barbara": "barb babs", "kimberly": "kim", "victoria": "vicky tori",
    "abigail": "abby", "madison": "maddie", "isabella": "bella izzy", "jacqueline": "jackie",
    "stephanie": "steph", "amanda": "mandy", "melissa": "missy mel", "pamela": "pam",
    "cynthia": "cindy", "theresa": "terry tess teresa", "dorothy": "dot dottie", "judith": "judy",
    "nancy": "nan", "sandra": "sandy", "angela": "angie", "kristopher": "kris",
    "gabriel": "gabe", "vincent": "vince", "jacob": "jake", "megan": "meg", "ashley": "ash",
    "allison": "ally allie", "danielle": "dani", "valerie": "val", "suzanne": "suzy",
}
NICKNAME_TO_GROUP: dict[str, str] = {}          # one key per name, for blocking and exact levels
NICKNAME_TO_GROUPS: dict[str, frozenset] = {}   # every formal name a nickname can stand for
for formal, nicks in _NICK_GROUPS.items():
    for n in [formal, *nicks.split()]:
        NICKNAME_TO_GROUP.setdefault(n, formal)
        NICKNAME_TO_GROUPS[n] = NICKNAME_TO_GROUPS.get(n, frozenset()) | {formal}

_NON_ALPHA = re.compile(r"[^a-z]")


def clean_name(s) -> str | None:
    if s is None or (isinstance(s, float) and s != s):
        return None
    v = _NON_ALPHA.sub("", str(s).lower())
    return v or None


def name_group(first: str | None) -> str | None:
    return NICKNAME_TO_GROUP.get(first, first) if first else None


def could_be_same_first_name(a: str, b: str) -> bool:
    """Jon can be John or Jonathan; a single group per nickname would wrongly rule one out."""
    return bool(NICKNAME_TO_GROUPS.get(a, frozenset({a})) & NICKNAME_TO_GROUPS.get(b, frozenset({b})))


def split_full_name(full) -> tuple[str | None, str | None]:
    """'SMITH, CANDACE' or 'Candace Smith' or 'Candace J Smith' -> (first, last)."""
    if full is None or (isinstance(full, float) and full != full):
        return None, None
    s = str(full).strip()
    if "," in s:
        last, first = s.split(",", 1)
        first = first.strip().split(" ")[0] if first.strip() else ""
        return clean_name(first), clean_name(last)
    parts = s.split()
    if len(parts) == 1:
        return None, clean_name(parts[0])
    return clean_name(parts[0]), clean_name(parts[-1])


def standardize_street(s) -> tuple[str | None, str | None, str | None]:
    """-> (house_number, street_name, unit) with USPS suffixes and directionals."""
    if s is None or (isinstance(s, float) and s != s) or not str(s).strip():
        return None, None, None
    v = re.sub(r"[.,]", " ", str(s).upper())
    unit = None
    m = re.search(rf"\s{UNIT_WORDS}\s*([A-Z0-9-]+)\s*$", v)
    if m:
        unit, v = m.group(1), v[: m.start()]
    toks = v.split()
    house = toks[0] if toks and re.match(r"^\d[\d-]*[A-Z]?$", toks[0]) else None
    rest = toks[1:] if house else toks
    rest = [DIRECTIONAL.get(t, USPS_SUFFIX.get(t, t)) for t in rest]
    rest = [re.sub(r"^(\d+)(ST|ND|RD|TH)$", r"\1", t) for t in rest]   # 18TH -> 18
    return house, (" ".join(rest) or None), unit


def email_user(e) -> str | None:
    if e is None or (isinstance(e, float) and e != e) or "@" not in str(e):
        return None
    return str(e).split("@", 1)[0].replace(".", "") or None
