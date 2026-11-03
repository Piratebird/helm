"""Project files that repeat each other must stay in step."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def pyproject_dependencies():
    text = (ROOT / "pyproject.toml").read_text()
    block = re.search(r"^dependencies = \[(.*?)^\]", text, re.S | re.M).group(1)
    return [line.strip().strip(",").strip('"') for line in block.splitlines() if line.strip()]


def test_requirements_txt_matches_pyproject():
    """The Dockerfile installs requirements.txt first to cache that layer; pyproject.toml is the source."""
    requirements = [
        line.strip()
        for line in (ROOT / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert requirements == pyproject_dependencies()


def test_ci_installs_tmux_and_tries_libtorrent():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "apt-get install -y tmux" in ci
    assert 'pip install "libtorrent>=2.0.0,<2.1.0" ||' in ci
    assert "requirements.txt" not in ci  # pyproject's [dev] extra already pulls everything in
