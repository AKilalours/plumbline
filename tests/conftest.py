"""Before any test module imports the project: is this the Python the project is installed in?

Run from Anaconda's base Python, the suite failed with twelve "No module named 'splink'/'faker'"
collection errors that pointed everywhere but the cause (the .venv was not active). This stops at the
door with one message that names the Python in use and the commands that fix it.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_PREFLIGHT = Path(__file__).resolve().parents[1] / "src" / "plumbline" / "app" / "preflight.py"


def _preflight():
    spec = importlib.util.spec_from_file_location("_plumbline_preflight", _PREFLIGHT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def missing_for_tests() -> list[str]:
    return [d for d in _preflight().missing() if d != "streamlit"]      # the app's tests skip without it


def pytest_configure(config):
    miss = missing_for_tests()
    if miss:
        repo = _PREFLIGHT.parents[3]
        pytest.exit(f"\nThese tests are running on {sys.executable}, which is missing: {', '.join(miss)}.\n"
                    f"Use the project's Python:\n\n    cd {repo}\n    source .venv/bin/activate\n"
                    f"    python -m pip install -e \".[dev,app]\"\n    python -m pytest -q\n", returncode=2)
