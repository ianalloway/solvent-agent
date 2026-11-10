"""docs/DEVELOPMENT.md's repository layout must list every solvent module (#133)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_every_module_is_in_the_layout_map():
    doc = (ROOT / "docs" / "DEVELOPMENT.md").read_text(encoding="utf-8")
    listed = set(re.findall(r"^  (\w+\.py)\s", doc, re.M))
    modules = {p.name for p in (ROOT / "solvent").glob("*.py")} - {"__init__.py"}

    assert sorted(modules - listed) == [], "add these to the Repository Layout map"
    assert sorted(listed - modules) == [], "these are listed but no longer exist"
