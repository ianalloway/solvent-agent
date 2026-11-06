"""Regression tests for POST /payments/nano/verify on the async (serve) path.

The server runs with ``sync_payment=False``, so the payment stage only books a
Nano job once the invoice row is marked paid with the verified block hash. The
verify endpoint must record that, and the checkout sweep must not expire a job
whose block was already verified (the block is single-use once booked).
"""

import time

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:
    pytest.skip("FastAPI test client is not installed", allow_module_level=True)

from solvent import checkout, server, treasury
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


def test_sweep_does_not_expire_a_verified_but_unadvanced_job(client, monkeypatch):
    agent = client.app.state.agent
    invoice = _nano_job(client, "nano-race")
    block = agent.nano.mint_sim_block(invoice)

    # Verify succeeds, then the process dies before the job is advanced.
    real_advance = agent.advance_job
    monkeypatch.setattr(agent, "advance_job", lambda job_id: {"stage": "interrupted"})
    response = client.post(
        "/payments/nano/verify",
        json={"job_id": "nano-race", "block_hash": block},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text

    # A sweep long after the invoice deadline must leave the job payable.
    result = checkout.sweep(agent.t, nano=agent.nano, now=time.time() + 30 * 86_400)
    assert "nano-race" not in result["expired"]
    assert agent.t.get_job("nano-race")["status"] == "awaiting_payment"

    # The next worker pass books the revenue from the recorded block.
    real_advance("nano-race")
    assert agent.t.job_has_revenue("nano-race")
