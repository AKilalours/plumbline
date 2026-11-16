"""Every picture and video the README links to is in the repo: a renamed file shows as a broken image on GitHub."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_every_local_file_the_readme_links_to_exists():
    text = (ROOT / "README.md").read_text()
    links = re.findall(r'(?:src|href)="([^"#:]+)"', text) + re.findall(r"\]\(([^)#:\s]+)\)", text)
    local = [l for l in links if not l.startswith(("http", "mailto"))]
    assert local, "no local links found: the pattern is wrong"
    missing = [l for l in local if not (ROOT / l).exists()]
    assert not missing, missing


def test_the_two_videos_are_in_the_repo_and_under_githubs_warning_size():
    for name in ("plumbline-how-it-works", "plumbline-using-it"):
        p = ROOT / "docs" / "video" / f"{name}.mp4"
        assert p.exists() and p.stat().st_size < 50 * 1024 * 1024    # GitHub warns above 50 MB
