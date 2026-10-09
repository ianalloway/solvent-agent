"""Security regressions for the Stripe webhook route and webhook admin routes.

* A webhook with a bad/missing signature stores and logs nothing.
* Only signature-verified events are stored; an event id is stored once.
* /api/webhooks, /api/webhooks/stats and /api/webhooks/{id}/replay need the
  dashboard token, and replay re-dispatches a stored verified event internally.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:
    pytest.skip("FastAPI test client is not installed", allow_module_level=True)

from solvent import server, treasury
from solvent.stripe_client import StripeClient
from solvent.webhook_log import WebhookLog

SECRET = "whsec_unit_test_secret"
TOKEN = "d" * 32
AUTH = {"X-Solvent-Dashboard-Token": TOKEN}


def _sign(payload: bytes, secret: str = SECRET, ts: int | None = None) -> str:
    ts = int(time.time()) if ts is None else ts
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def _event(event_id: str = "evt_1", job_id: str = "J-wh", email: str = "victim@example.com"):
    return json.dumps(
        {
            "id": event_id,
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_1",
                    "payment_status": "paid",
                    "payment_intent": "pi_1",
                    "amount_total": 4900,
                    "currency": "usd",
                    "customer_email": email,
                    "metadata": {"job_id": job_id},
                    "client_reference_id": job_id,
                }
            },
        }
    ).encode()


class _LiveStripe(StripeClient):
    """StripeClient in 'live' webhook mode without needing a Stripe key/package."""

    secret = SECRET

    def __init__(self):
        super().__init__()
        self.live = True
        self.webhook_secret = self.secret


class _UnconfiguredStripe(StripeClient):
    def __init__(self):
        super().__init__()
        self.live = True  # live, but no webhook secret
        self.webhook_secret = ""


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    monkeypatch.setenv("SOLVENT_DASHBOARD_TOKEN", TOKEN)
    monkeypatch.setenv("SOLVENT_DELIVERY_SECRET", "test-delivery-secret-" * 2)
    monkeypatch.setenv("SOLVENT_FORCE_STRIPE_SIMULATE", "1")
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setattr(treasury, "DB_PATH", tmp_path / "solvent.db")
    log = WebhookLog(":memory:")
    monkeypatch.setattr(server, "WebhookLog", lambda: log)

    def build(stripe_cls=_LiveStripe):
        monkeypatch.setattr(server, "StripeClient", stripe_cls)
        return TestClient(server.create_app(fresh=True)), log

    return build


def _rows(log: WebhookLog) -> list[dict]:
    return log.list_recent(100)


# ---------------------------------------------------------------------------
# (1) signature verified first; only verified events stored
# ---------------------------------------------------------------------------


def test_bad_signature_is_rejected_and_nothing_is_stored(make_client):
    client, log = make_client()
    payload = _event("evt_forged")
    for headers in (
        {"Stripe-Signature": _sign(payload, secret="whsec_wrong")},
        {"Stripe-Signature": "t=1,v1=deadbeef"},
        {"Stripe-Signature": _sign(payload, ts=int(time.time()) - 3600)},  # stale
        {},  # missing header
    ):
        resp = client.post("/webhooks/stripe", content=payload, headers=headers)
        assert resp.status_code == 400
        assert "victim@example.com" not in resp.text
    assert _rows(log) == []
    assert log.stats()["total"] == 0


def test_garbage_body_with_no_signature_leaves_no_trace(make_client):
    client, log = make_client()
    for body in (b"", b"not json", b"[]", b'{"id": 5}'):
        resp = client.post("/webhooks/stripe", content=body)
        assert resp.status_code == 400
    assert _rows(log) == []


def test_validly_signed_but_idless_body_is_rejected(make_client):
    client, log = make_client()
    payload = b'{"type": "checkout.session.completed"}'
    resp = client.post(
        "/webhooks/stripe", content=payload, headers={"Stripe-Signature": _sign(payload)}
    )
    assert resp.status_code == 400
    assert _rows(log) == []


def test_live_mode_without_webhook_secret_fails_closed(make_client):
    client, log = make_client(_UnconfiguredStripe)
    payload = _event("evt_nosecret")
    resp = client.post(
        "/webhooks/stripe", content=payload, headers={"Stripe-Signature": _sign(payload)}
    )
    assert resp.status_code == 503
    resp = client.post("/webhooks/stripe", content=payload)
    assert resp.status_code == 503
    assert _rows(log) == []


def test_offline_demo_default_client_rejects_and_stores_nothing(make_client):
    client, log = make_client(StripeClient)  # simulator: no key, no secret
    payload = _event("evt_offline")
    resp = client.post(
        "/webhooks/stripe", content=payload, headers={"Stripe-Signature": _sign(payload)}
    )
    assert resp.status_code == 503
    assert _rows(log) == []
    # the rest of the offline app still works
    assert client.get("/health").status_code == 200


def test_good_signature_stores_exactly_once_and_marks_verified(make_client):
    client, log = make_client()
    payload = _event("evt_good")
    resp = client.post(
        "/webhooks/stripe", content=payload, headers={"Stripe-Signature": _sign(payload)}
    )
    assert resp.status_code == 200
    assert resp.json() == {"received": True}
    rows = _rows(log)
    assert len(rows) == 1
    assert rows[0]["event_id"] == "evt_good"
    assert rows[0]["status"] == "processed"
    assert log.is_verified("evt_good")
    assert log.get_payload("evt_good") == payload


def test_duplicate_event_id_is_idempotent_and_never_overwrites(make_client):
    client, log = make_client()
    first = _event("evt_dup", email="first@example.com")
    assert (
        client.post(
            "/webhooks/stripe", content=first, headers={"Stripe-Signature": _sign(first)}
        ).status_code
        == 200
    )
    # Same id, different (even correctly signed) body: acknowledged, not stored.
    second = _event("evt_dup", email="second@example.com")
    resp = client.post(
        "/webhooks/stripe", content=second, headers={"Stripe-Signature": _sign(second)}
    )
    assert resp.status_code == 200
    assert resp.json()["duplicate"] is True
    assert len(_rows(log)) == 1
    assert log.get_payload("evt_dup") == first
    # And an unsigned attempt at the same id is still rejected outright.
    forged = _event("evt_dup", email="evil@example.com")
    assert client.post("/webhooks/stripe", content=forged).status_code == 400
    assert log.get_payload("evt_dup") == first


def test_failed_event_can_be_retried_by_stripe(make_client, monkeypatch):
    client, log = make_client()
    payload = _event("evt_retry")
    calls = {"n": 0}
    real = _LiveStripe.apply_webhook_event

    def flaky(self, event, treasury=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return real(self, event, treasury=treasury)

    monkeypatch.setattr(_LiveStripe, "apply_webhook_event", flaky)
    headers = {"Stripe-Signature": _sign(payload)}
    with pytest.raises(RuntimeError):
        client.post("/webhooks/stripe", content=payload, headers=headers)
    assert log.get_status("evt_retry") == "error"
    resp = client.post("/webhooks/stripe", content=payload, headers=headers)
    assert resp.status_code == 200
    assert log.get_status("evt_retry") == "processed"
    assert len(_rows(log)) == 1


# ---------------------------------------------------------------------------
# (2) admin routes require the dashboard token; replay works internally
# ---------------------------------------------------------------------------


def _store_verified(client, event_id="evt_stored"):
    payload = _event(event_id)
    resp = client.post(
        "/webhooks/stripe", content=payload, headers={"Stripe-Signature": _sign(payload)}
    )
    assert resp.status_code == 200
    return payload


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/webhooks"),
        ("get", "/api/webhooks/stats"),
        ("post", "/api/webhooks/evt_stored/replay"),
    ],
)
def test_webhook_admin_routes_require_dashboard_token(make_client, method, path):
    client, _log = make_client()
    _store_verified(client)
    call = getattr(client, method)
    assert call(path).status_code in (401, 403)
    assert call(path, headers={"X-Solvent-Dashboard-Token": "wrong"}).status_code in (401, 403)
    assert call(path + "?token=wrong").status_code in (401, 403)
    assert call(path, headers=AUTH).status_code == 200
    assert call(path + f"?token={TOKEN}").status_code == 200


def test_webhook_admin_routes_denied_when_no_token_configured(make_client, monkeypatch):
    monkeypatch.setenv("SOLVENT_DASHBOARD_TOKEN", "")
    client, _log = make_client()
    for method, path in (
        ("get", "/api/webhooks"),
        ("get", "/api/webhooks/stats"),
        ("post", "/api/webhooks/evt_x/replay"),
    ):
        assert getattr(client, method)(path).status_code in (401, 403)
        assert getattr(client, method)(path, headers={"X-Solvent-Dashboard-Token": ""}).status_code in (
            401,
            403,
        )


def test_list_and_stats_return_metadata_without_payloads(make_client):
    client, _log = make_client()
    _store_verified(client)
    listed = client.get("/api/webhooks", headers=AUTH)
    assert listed.status_code == 200
    rows = listed.json()
    assert rows[0]["event_id"] == "evt_stored"
    assert "payload" not in rows[0]
    assert "victim@example.com" not in listed.text
    stats = client.get("/api/webhooks/stats", headers=AUTH).json()
    assert stats["total"] == 1
    assert stats["processed"] == 1


def test_replay_redispatches_stored_verified_event_without_signature(make_client, monkeypatch):
    client, log = make_client()
    _store_verified(client)
    applied = []
    real = _LiveStripe.apply_webhook_event

    def spy(self, event, treasury=None):
        applied.append(event["id"])
        return real(self, event, treasury=treasury)

    monkeypatch.setattr(_LiveStripe, "apply_webhook_event", spy)
    log.mark_error("evt_stored", "earlier failure")
    resp = client.post("/api/webhooks/evt_stored/replay", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json() == {"replayed": True, "event_id": "evt_stored"}
    assert applied == ["evt_stored"]
    assert log.get_status("evt_stored") == "processed"


def test_replay_unknown_event_is_404(make_client):
    client, _log = make_client()
    assert client.post("/api/webhooks/evt_nope/replay", headers=AUTH).status_code == 404


def test_replay_refuses_unverified_legacy_rows(make_client):
    client, log = make_client()
    # A row planted by the old unauthenticated route (or any unverified writer).
    log.record("evt_planted", "checkout.session.completed", _event("evt_planted"), "received")
    resp = client.post("/api/webhooks/evt_planted/replay", headers=AUTH)
    assert resp.status_code == 409
    assert log.get_status("evt_planted") == "received"


def test_unverified_planted_row_does_not_block_the_real_event(make_client):
    client, log = make_client()
    log.record("evt_race", "checkout.session.completed", b"{}", "processed")  # unverified
    payload = _event("evt_race")
    resp = client.post(
        "/webhooks/stripe", content=payload, headers={"Stripe-Signature": _sign(payload)}
    )
    assert resp.status_code == 200
    assert "duplicate" not in resp.json()
