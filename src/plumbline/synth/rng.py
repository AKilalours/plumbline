"""One independent random stream per component.

Spawning children from one SeedSequence means a change inside, say, the email
generator cannot shift the random numbers the people generator sees. Append new
components at the END of this tuple: spawn order is positional, so inserting one in
the middle would silently change every stream after it.
"""
from __future__ import annotations

import numpy as np

COMPONENTS = (
    "households", "people", "names", "contacts", "jobs",
    "events", "crm", "vendor", "email", "privacy",
)


def component_rngs(seed: int) -> dict[str, np.random.Generator]:
    children = np.random.SeedSequence(seed).spawn(len(COMPONENTS))
    return {name: np.random.default_rng(s) for name, s in zip(COMPONENTS, children)}
