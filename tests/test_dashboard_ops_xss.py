"""Ops panel / inline-script escaping tests for the dashboard."""

import json

from solvent import dashboard
from solvent import treasury as treasury_mod

PAYLOAD = '<img src=x onerror="globalThis.__SOLVENT_XSS_POC=1">'
ESCAPED = "&lt;img src=x onerror=&quot;globalThis.__SOLVENT_XSS_POC=1&quot;&gt;"


class _FakeTreasury:
    def __init__(self, *args, **kwargs):
        pass

    def list_jobs(self):
        return [{"id": PAYLOAD, "status": "in_progress", "topic": PAYLOAD[:40]}]

    def list_metrics(self):
        return [
            {
                "job_id": PAYLOAD,
                "est_margin_pct": PAYLOAD,
                "actual_margin_pct": 10,
                "margin_drift_cents": 5,
            }
        ]


def _snapshot():
    return {
        "capital_cents": 10000,
        "balance_cents": 10000,
        "revenue_cents": 0,
        "expense_cents": 0,
        "net_profit_cents": 0,
        "margin_pct": 0.0,
        "entries": [{"kind": "capital", "amount_cents": 10000}],
    }


def test_ops_panel_escapes_stuck_job_and_drift_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    monkeypatch.setattr(treasury_mod, "Treasury", _FakeTreasury)

    status = dashboard.build_status_data(_snapshot(), [])
    ops_html = status["ops_html"]

    assert "Stuck jobs" in ops_html
    assert "Margin drift" in ops_html
    assert "<img" not in ops_html
    assert "&lt;img" in ops_html
    assert ESCAPED in ops_html  # job id / est margin escaped in full


def test_inline_script_json_cannot_close_script_tag(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    monkeypatch.setattr(dashboard, "OUT", tmp_path / "treasury_dashboard.html")

    breakout = "</script><script>globalThis.__SOLVENT_XSS_POC=1</script>"
    log = [
        {
            "stage": "quote",
            "job_id": "J1",
            "title": breakout,
            "price": 5000,
            "est_cost": 3000,
            "margin_pct": 40.0,
            "accept": True,
            "ts": 1,
        }
    ]

    out = dashboard.render(_snapshot(), log)
    page = out.read_text()

    assert breakout not in page
    assert "</script><script>globalThis" not in page
    # The data still round-trips as JSON for the client.
    assert json.loads(dashboard._script_json({"t": breakout}))["t"] == breakout


def test_inline_script_json_escapes_js_line_terminators():
    encoded = dashboard._script_json({"t": "a b c"})
    assert " " not in encoded and " " not in encoded
    assert json.loads(encoded)["t"] == "a b c"


def _js_function(page: str, name: str) -> str:
    start = page.index(f"function {name}(")
    return page[start : page.index("\n    }\n", start)]


def test_client_renders_job_fields_as_text_not_markup(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    monkeypatch.setattr(dashboard, "OUT", tmp_path / "treasury_dashboard.html")
    page = dashboard.render(_snapshot(), []).read_text()

    for name in ("updateBriefsList", "updateDrawerContent"):
        body = _js_function(page, name)
        assert "textContent" in body
        assert "metaHTML" not in body
        assert "container.innerHTML = html" not in body
        assert "onclick=" not in body


def test_rendered_inline_scripts_parse_as_javascript(tmp_path, monkeypatch):
    import re
    import shutil
    import subprocess

    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    monkeypatch.setattr(dashboard, "OUT", tmp_path / "treasury_dashboard.html")
    page = dashboard.render(_snapshot(), []).read_text()

    scripts = re.findall(r"<script>(.*?)</script>", page, re.S)
    assert scripts
    for index, script in enumerate(scripts):
        path = tmp_path / f"inline-{index}.js"
        path.write_text(script, encoding="utf-8")
        result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    assert "html.split(/\\n\\n+/)" in page
