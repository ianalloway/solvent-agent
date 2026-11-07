"""Regression tests for POST /payments/nano/verify on the async (serve) path.

The server runs with ``sync_payment=False``. A verified block must reach the
ledger exactly once: an interrupted verify must leave the proof unconsumed so
the same block can be resubmitted, and a resubmit after booking must not book
it again (regression for #141, where verified blocks were never booked).
"""

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:
    pytest.skip("FastAPI test client is not installed", allow_module_level=True)

from solvent import server, treasury
from solvent.webhook_log import WebhookLog

TOKEN = "d" * 32
HEADERS = {"X-Solvent-Dashboard-Token": TOKEN}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    monkeypatch.setenv("SOLVENT_DASHBOARD_TOKEN", TOKEN)
    monkeypatch.setenv("SOLVENT_DELIVERY_SECRET", "test-delivery-secret-" * 2)
    monkeypatch.setenv("SOLVENT_FORCE_STRIPE_SIMULATE", "1")
    monkeypatch.setenv("SOLVENT_NANO", "sim")
    for name in ("SOLVENT_NANO_RPC", "SOLVENT_NANO_ADDRESS", "SOLVENT_FORCE_NANO_SIMULATE"):
        monkeypatch.setenv(name, "")
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setattr(treasury, "DB_PATH", tmp_path / "solvent.db")
    monkeypatch.setattr(server, "WebhookLog", lambda: WebhookLog(":memory:"))
    return TestClient(server.create_app(fresh=True))


def _nano_job(client, job_id: str) -> dict:
    response = client.post(
        "/api/job",
        json={
            "id": job_id,
            "topic": "AI chip market",
            "budget_cents": 4900,
            "customer_email": f"{job_id}@example.test",
        },
        headers=HEADERS,
    )
    assert response.status_code == 200
    agent = client.app.state.agent
    assert agent.t.get_job(job_id)["status"] == "awaiting_payment"
    invoice = agent.t.get_nano_invoice(job_id)
    assert invoice, "nano rail should have issued an invoice"
    return {**invoice, "id": invoice["invoice_id"], "job_id": job_id}


def test_verified_block_books_the_payment(client):
    agent = client.app.state.agent
    invoice = _nano_job(client, "nano-paid")
    block = agent.nano.mint_sim_block(invoice)

    response = client.post(
        "/payments/nano/verify",
        json={"job_id": "nano-paid", "block_hash": block},
        headers=HEADERS,
    )

    assert response.status_code == 200, response.text
    assert agent.t.job_has_revenue("nano-paid")
    assert agent.t.get_job("nano-paid")["status"] != "awaiting_payment"
    assert agent.t.get_nano_invoice("nano-paid")["status"] == "paid"


def _revenue_rows(agent, job_id: str) -> int:
    return sum(e.kind == "revenue" and e.job_id == job_id for e in agent.t.entries)


def test_interrupted_verify_can_be_resubmitted_and_books_once(client, monkeypatch):
    agent = client.app.state.agent
    invoice = _nano_job(client, "nano-retry")
    block = agent.nano.mint_sim_block(invoice)

    def submit():
        return client.post(
            "/payments/nano/verify",
            json={"job_id": "nano-retry", "block_hash": block},
            headers=HEADERS,
        )

    # The process dies after verification but before the job is advanced.
    real_advance = agent.advance_job
    monkeypatch.setattr(agent, "advance_job", lambda job_id, **kwargs: {"stage": "interrupted"})
    assert submit().status_code == 409
    assert not agent.nano.is_block_booked(block)
    assert not agent.t.job_has_revenue("nano-retry")

    # The customer's same block still pays for the job, and only once.
    monkeypatch.setattr(agent, "advance_job", real_advance)
    assert submit().status_code == 200
    submit()
    assert _revenue_rows(agent, "nano-retry") == 1
