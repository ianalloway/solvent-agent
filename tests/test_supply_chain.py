"""Supply-chain guards: pinned Actions, least-privilege workflows, audited floors."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
USES = re.compile(r"^\s*-?\s*uses:\s*(\S+)", re.M)


def test_every_action_is_pinned_to_a_full_sha() -> None:
    assert WORKFLOWS
    for wf in WORKFLOWS:
        for ref in USES.findall(wf.read_text(encoding="utf-8")):
            if ref.startswith("./"):
                continue
            _, _, version = ref.partition("@")
            assert re.fullmatch(r"[0-9a-f]{40}", version), f"{wf.name}: {ref} is not SHA-pinned"


def test_every_workflow_has_top_level_read_only_permissions() -> None:
    for wf in WORKFLOWS:
        text = wf.read_text(encoding="utf-8")
        assert re.search(r"^permissions:\n  contents: read\n", text, re.M), wf.name


def test_publish_keeps_trusted_publishing_scope() -> None:
    text = (ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
    assert "id-token: write" in text


def test_security_floors_present() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for floor in ("urllib3>=2.8.0", "certifi>=2026.10.19", "anyio>=4.14.2", "pillow>=13.0.0"):
        assert floor in pyproject, floor
    constraints = (ROOT / "constraints.txt").read_text(encoding="utf-8")
    for pin in ("urllib3==", "certifi==", "anyio==", "pillow=="):
        assert pin in constraints, pin
