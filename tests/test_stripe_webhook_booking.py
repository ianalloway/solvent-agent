"""POST /webhooks/stripe must book exactly the payment it was promised.

``test_webhook_security`` covers authentication (signatures, size limits,
duplicate ids). These tests cover the money: a signed webhook for an open
checkout books revenue once, while an underpaid, mismatched or replayed event
books nothing extra. They drive the real endpoint, like
``test_nano_verify_endpoint`` does for the Nano rail.
"""

import hashlib
import hmac
import json
import time
from dataclasses import asdict

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:
    pytest.skip("FastAPI test client is not installed", allow_module_level=True)

from solvent import server, treasury
from solvent.pricing import quote
from solvent.stripe_client import StripeClient
from solvent.webhook_log import WebhookLog

SECRET = "whsec_unit_test_secret"
TOKEN = "d" * 32
AUTH = {"X-Solvent-Dashboard-Token": TOKEN}
SESSION = "cs_test_booking"


class _LiveStripe(StripeClient):
    """Live webhook mode without a Stripe key or the stripe package."""

    def __init__(self):
        super().__init__()
        self.live = True
        self.webhook_secret = SECRET


def _sign(payload: bytes) -> str:
    ts = int(time.time())
    mac = hmac.new(SECRET.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def _event(event_id: str, job_id: str, amount: int, session: str = SESSION) -> bytes:
    return json.dumps(
        {
            "id": event_id,
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": session,
                    "payment_status": "paid",
                    "payment_intent": f"pi_{event_id}",
                    "amount_total": amount,
                    "currency": "usd",
                    "customer_email": "buyer@example.com",
                    "metadata": {"job_id": job_id},
                    "client_reference_id": job_id,
                }
            },
        }
    ).encode()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    monkeypatch.setenv("SOLVENT_DASHBOARD_TOKEN", TOKEN)
    monkeypatch.setenv("SOLVENT_DELIVERY_SECRET", "test-delivery-secret-" * 2)
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setattr(treasury, "DB_PATH", tmp_path / "solvent.db")
    monkeypatch.setattr("solvent.delivery.OUTBOX_DIR", tmp_path / "outbox")
    monkeypatch.setattr(server, "WebhookLog", lambda: WebhookLog(":memory:"))
    monkeypatch.setattr(server, "StripeClient", _LiveStripe)
    return TestClient(server.create_app(fresh=True))


def _open_checkout(client, job_id: str) -> int:
    """Put ``job_id`` in awaiting_payment with an open session; return its price."""
    agent = client.app.state.agent
    job = {
        "id": job_id,
        "topic": "Battery markets",
        "budget_cents": 5000,
        "customer_email": "buyer@example.com",
        "est_tokens": 8000,
        "market_data_calls": 2,
        "web_search_calls": 6,
    }
    q = quote(job, agent.pricing)
    assert q.accept
    agent.t.upsert_job(
        job_id,
        "awaiting_payment",
        topic=job["topic"],
        budget_cents=job["budget_cents"],
        customer_email=job["customer_email"],
        quote_json=json.dumps(asdict(q)),
        checkout_session_id=SESSION,
        checkout_url="https://example.invalid/checkout",
    )
    agent.t.upsert_checkout(job_id, SESSION, "https://example.invalid/checkout", "open")
    return q.price_cents


def _post(client, payload: bytes):
    return client.post("/webhooks/stripe", content=payload, headers={"Stripe-Signature": _sign(payload)})


def _revenue(client, job_id: str) -> list[int]:
    entries = client.app.state.agent.t.entries
    return [e.amount_cents for e in entries if e.kind == "revenue" and e.job_id == job_id]


def test_signed_payment_books_the_quoted_revenue_once(client):
    price = _open_checkout(client, "wh-paid")

    assert _post(client, _event("evt_paid", "wh-paid", price)).status_code == 200

    assert _revenue(client, "wh-paid") == [price]
    assert client.app.state.agent.t.get_job("wh-paid")["status"] != "awaiting_payment"


def test_underpaid_session_books_nothing(client):
    price = _open_checkout(client, "wh-short")

    assert _post(client, _event("evt_short", "wh-short", price - 1)).status_code == 200

    assert _revenue(client, "wh-short") == []
    assert client.app.state.agent.t.get_job("wh-short")["status"] == "awaiting_payment"


def test_payment_for_another_session_books_nothing(client):
    price = _open_checkout(client, "wh-other")

    payload = _event("evt_other", "wh-other", price, session="cs_test_someone_else")
    assert _post(client, payload).status_code == 200

    assert _revenue(client, "wh-other") == []


def test_redelivery_and_operator_replay_do_not_book_twice(client):
    price = _open_checkout(client, "wh-dup")
    payload = _event("evt_dup", "wh-dup", price)

    assert _post(client, payload).status_code == 200
    assert _post(client, payload).json().get("duplicate") is True
    assert client.post("/api/webhooks/evt_dup/replay", headers=AUTH).status_code == 200

    assert _revenue(client, "wh-dup") == [price]


def test_a_payment_cached_by_one_client_is_seen_by_another(tmp_path, monkeypatch):
    """``serve`` caches on its own client; the runner and ``solvent worker`` hold others."""
    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    runner = StripeClient()  # created first, like the worker at startup
    webhooks = StripeClient()
    payment = {"paid": True, "stripe_ref": "pi_x", "job_id": "J-shared"}

    webhooks._cache_webhook_payment("job:J-shared", payment)

    assert runner.get_cached_payment("J-shared") == payment


def test_an_unreadable_cache_file_keeps_what_is_in_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    client = StripeClient()
    client._webhook_payments["job:J-mem"] = {"paid": True, "job_id": "J-mem"}
    from solvent.paths import config_path

    path = config_path("stripe_payments.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{half-written", encoding="utf-8")

    assert client.get_cached_payment("J-other") is None
    assert client.get_cached_payment("J-mem") == {"paid": True, "job_id": "J-mem"}
