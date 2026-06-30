"""Security tests for dashboard rendering."""

import json

from solvent import dashboard


def test_dashboard_status_html_fragments_escape_untrusted_fields(tmp_path, monkeypatch):
    # Redirect all runtime data to a temp home so the status JSON lands there.
    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    monkeypatch.setattr(dashboard, "OUT", tmp_path / "treasury_dashboard.html")

    payload = '<img src=x onerror="globalThis.__SOLVENT_XSS_POC=1">'
    snapshot = {
        "capital_cents": 10000,
        "balance_cents": 12000,
        "revenue_cents": 5000,
        "expense_cents": 3000,
        "net_profit_cents": 2000,
        "margin_pct": 40.0,
        "entries": [
            {"kind": "capital", "amount_cents": 10000},
            {"kind": "expense", "amount_cents": 3000, "vendor": payload},
        ],
    }
    log = [
        {
            "stage": "quote",
            "job_id": "J1",
            "title": payload,
            "price": 5000,
            "est_cost": 3000,
            "margin_pct": 40.0,
            "accept": False,
            "reason": payload,
            "ts": 1,
        },
        {"stage": "declined", "job_id": "J1", "reason": payload, "ts": 2},
        {
            "stage": "invoice",
            "job_id": "J1",
            "amount": 5000,
            "simulated": True,
            "url": "javascript:alert(1)",
            "ts": 2,
        },
        {
            "stage": "spend_blocked",
            "job_id": "J1",
            "amount": 1000,
            "vendor": payload,
            "memo": payload,
            "ts": 3,
        },
    ]

    dashboard.render(snapshot, log)

    status_path = tmp_path / "data" / "dashboard_status.json"
    status = json.loads(status_path.read_text())
    rendered_fragments = "".join(
        [
            status["expense_breakdown_html"],
            status["job_cards_html"],
            status["console_log_html"],
        ]
    )

    assert payload not in rendered_fragments
    assert "&lt;img src=x onerror=&quot;globalThis.__SOLVENT_XSS_POC=1&quot;&gt;" in rendered_fragments
    assert "javascript:alert(1)" not in rendered_fragments
    assert "href='#'" in rendered_fragments or 'href="#"' in rendered_fragments
