"""Before the app imports anything heavy: is it running on a Python that has the project installed?

The first hosted test of the app crashed on every page with "No module named 'faker'". The code was
fine; `streamlit` on the PATH was Anaconda's, a different Python from the project's .venv, so none
of the project's libraries were there. The app had put src/ on sys.path itself, so plumbline still
imported, and the failure surfaced deep in the generator instead of at the door. This check runs
first, on the standard library only, and says which Python is running and how to start the right one.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
# distribution name (as in pyproject.toml) -> module it installs
IMPORT_NAME = {"numpy": "numpy", "pandas": "pandas", "pyarrow": "pyarrow", "faker": "faker", "duckdb": "duckdb",
               "pyyaml": "yaml", "splink": "splink", "dbt-core": "dbt", "dbt-duckdb": "dbt.adapters.duckdb",
               "scikit-learn": "sklearn", "scipy": "scipy", "streamlit": "streamlit", "pydantic": "pydantic",
               "sqlglot": "sqlglot"}


def required(pyproject: Path = REPO / "pyproject.toml") -> list[str]:
    """The project's own dependencies plus the app's, read from pyproject.toml so the two never drift."""
    text = pyproject.read_text()
    names = []
    for block in (r"^dependencies = \[(.*?)\]", r"^app = \[(.*?)\]"):
        m = re.search(block, text, re.S | re.M)
        # each requirement is one quoted string; version ranges hold commas ("splink>=4.0.6,<5")
        names += [re.split(r"[<>=!~\[; ]", spec)[0].strip().lower() for spec in re.findall(r'"([^"]+)"', m.group(1))] if m else []
    return names


def _found(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except ModuleNotFoundError:          # a parent package is missing ("dbt" for "dbt.adapters.duckdb")
        return False


def missing(pyproject: Path = REPO / "pyproject.toml") -> list[str]:
    """Distributions whose module this Python cannot import."""
    # A dependency missing from IMPORT_NAME is checked by its own name rather than crashing the page:
    # a long-running server once read a new pyproject.toml with an old table and raised KeyError.
    return [d for d in required(pyproject) if not _found(IMPORT_NAME.get(d, d.replace("-", "_")))]


def advice(miss: list[str]) -> tuple[str, str, str]:
    """-> (what is wrong, the commands that fix it, why those commands)."""
    return (f"This app is running on {sys.executable}, which is missing: {', '.join(miss)}.",
            f"cd {REPO}\nsource .venv/bin/activate\npython -m pip install -e \".[app]\"\npython -m streamlit run streamlit_app.py",
            "`python -m streamlit` runs Streamlit on whichever Python is active, so it cannot pick up another "
            "installation the way a bare `streamlit` command can.")
