"""Security regressions for the HTTP routes that were reachable without auth.

* /api/pair/qr (mint) needs the dashboard token; /api/pair/verify is open but
  single-use, attempt-limited and body-limited.
* POST /jobs is public intake: input-limited, whitelisted fields, sanitized reply.
* GET /jobs/{id} is a minimal public status view; the full row needs the token.
* /health exposes no treasury balance to anonymous callers.
* Local-only routes cannot be reached through a reverse proxy.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from solvent.access import FailureLimiter, is_local_request, local_access_mode

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - optional extra
    TestClient = None

TOKEN = "d" * 32
AUTH = {"X-Solvent-Dashboard-Token": TOKEN}
PROXY = {"X-Forwarded-For": "203.0.113.50"}


# ---------------------------------------------------------------------------
# is_local_request / FailureLimiter (no web framework needed)
# ---------------------------------------------------------------------------


def _req(host: str | None = "127.0.0.1", headers: dict | None = None):
    client = SimpleNamespace(host=host) if host is not None else None
    return SimpleNamespace(client=client, headers={k.lower(): v for k, v in (headers or {}).items()})


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1", "127.0.0.2"])
def test_loopback_peer_without_proxy_headers_is_local(host):
    assert is_local_request(_req(host), env={})


@pytest.mark.parametrize("host", ["203.0.113.9", "10.0.0.5", "192.168.1.2", "", None, "not-an-ip"])
def test_non_loopback_peer_is_not_local(host):
    assert not is_local_request(_req(host), env={})


def test_no_request_is_not_local():
    assert not is_local_request(None, env={})


@pytest.mark.parametrize(
    "header",
    [
        "X-Forwarded-For",
        "Forwarded",
        "X-Real-IP",
        "X-Forwarded-Host",
        "X-Forwarded-Proto",
        "CF-Connecting-IP",
        "True-Client-IP",
        "Via",
    ],
)
def test_proxy_headers_make_a_loopback_peer_non_local(header):
    # A reverse proxy on the same host makes every request arrive from 127.0.0.1.
    assert not is_local_request(_req("127.0.0.1", {header: "203.0.113.7"}), env={})


def test_local_access_env_override():
    proxied = _req("127.0.0.1", PROXY)
    plain = _req("127.0.0.1")
    assert local_access_mode({}) == "auto"
    assert local_access_mode({"SOLVENT_LOCAL_ACCESS": "bogus"}) == "auto"
    # never: nothing is local, even a plain loopback request.
    never = {"SOLVENT_LOCAL_ACCESS": "never"}
    assert not is_local_request(plain, env=never)
    assert not is_local_request(proxied, env=never)
    # peer: legacy behaviour, trusts the peer address only.
    peer = {"SOLVENT_LOCAL_ACCESS": " PEER "}
    assert is_local_request(plain, env=peer)
    assert is_local_request(proxied, env=peer)
    assert not is_local_request(_req("203.0.113.9"), env=peer)


def test_failure_limiter_blocks_then_recovers():
    lim = FailureLimiter(max_failures=3, window=10.0)
    for i in range(3):
        assert not lim.blocked("a", now=100.0 + i)
        lim.record_failure("a", now=100.0 + i)
    assert lim.blocked("a", now=103.0)
    assert not lim.blocked("b", now=103.0)  # per key
    assert not lim.blocked("a", now=111.5)  # window slid past the failures


def test_failure_limiter_is_bounded():
    lim = FailureLimiter(max_failures=1, window=1000.0, max_keys=5)
    for i in range(50):
        lim.record_failure(f"k{i}", now=float(i))
    assert len(lim._hits) <= 5


# ---------------------------------------------------------------------------
# HTTP routes
# ---------------------------------------------------------------------------

pytestmark_http = pytest.mark.skipif(TestClient is None, reason="FastAPI test client not installed")


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    from solvent import server, treasury
    from solvent.webhook_log import WebhookLog

    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    monkeypatch.setenv("SOLVENT_DASHBOARD_TOKEN", TOKEN)
    monkeypatch.setenv("SOLVENT_DELIVERY_SECRET", "test-delivery-secret-" * 2)
    monkeypatch.setenv("SOLVENT_FORCE_STRIPE_SIMULATE", "1")
    monkeypatch.delenv("SOLVENT_LOCAL_ACCESS", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setattr(treasury, "DB_PATH", tmp_path / "solvent.db")
    monkeypatch.setattr(server, "WebhookLog", lambda: WebhookLog(":memory:"))

    def build(**kwargs):
        app = server.create_app(fresh=True)
        return TestClient(app, **kwargs)

    return build


JOB = {
    "topic": "AI chip market",
    "budget_cents": 4900,
    "customer_email": "buyer@example.test",
}


def _job(**over):
    return {**JOB, **over}


# --- /health -----------------------------------------------------------------


@pytestmark_http
def test_health_is_minimal_without_auth(make_client):
    from solvent import __version__

    client = make_client()
    body = client.get("/health").json()
    assert body == {"status": "ok", "version": __version__}
    assert "balance_cents" not in client.get("/health", headers={"X-Solvent-Dashboard-Token": "wrong"}).json()


@pytestmark_http
def test_health_has_balance_with_dashboard_token(make_client):
    client = make_client()
    body = client.get("/health", headers=AUTH).json()
    assert body["status"] == "ok"
    assert isinstance(body["balance_cents"], int)
    assert client.get(f"/health?token={TOKEN}").json()["balance_cents"] == body["balance_cents"]


@pytestmark_http
def test_non_ascii_token_is_rejected_not_a_500(make_client):
    client = make_client()
    # Raw latin-1 bytes on the wire; compare_digest raises TypeError on such a str.
    resp = client.get("/api/status", headers={"X-Solvent-Dashboard-Token": ("é" * 32).encode("latin-1")})
    assert resp.status_code == 403
    assert client.get("/api/status", params={"token": "é"}).status_code == 403


# --- pairing -----------------------------------------------------------------


@pytestmark_http
def test_pair_qr_requires_dashboard_token(make_client):
    client = make_client()
    assert client.get("/api/pair/qr").status_code == 403
    assert client.get("/api/pair/qr", headers={"X-Solvent-Dashboard-Token": "nope"}).status_code == 403
    # Not even from loopback / behind a proxy header: the token is what matters.
    assert client.get("/api/pair/qr", headers=PROXY).status_code == 403


@pytestmark_http
def test_pair_qr_mints_a_token_for_the_operator(make_client):
    client = make_client()
    resp = client.get("/api/pair/qr", headers=AUTH)
    assert resp.status_code == 200
    # PNG when `qrcode` is installed, JSON fallback otherwise; both are fine.
    assert resp.headers["content-type"] in ("image/png", "application/json")
    # The browser flow passes the token as a query parameter.
    assert client.get(f"/api/pair/qr?token={TOKEN}").status_code == 200


@pytestmark_http
def test_pairing_flow_end_to_end_is_single_use(make_client):
    client = make_client()
    # Mint exactly like the QR route does (the PNG hides the token), then redeem.
    from solvent import server  # noqa: F401  (route table built in make_client)

    token = client.app.state.agent.t.create_openclaw_token(ttl=600)
    first = client.post("/api/pair/verify", json={"token": token})
    assert first.status_code == 200 and first.json() == {"verified": True}
    again = client.post("/api/pair/verify", json={"token": token})
    assert again.status_code == 403


@pytestmark_http
def test_pair_verify_rejects_unknown_and_expired_tokens(make_client):
    client = make_client()
    t = client.app.state.agent.t
    assert client.post("/api/pair/verify", json={"token": "OC-DEADBEEF"}).status_code == 403
    expired = t.create_openclaw_token(ttl=-1)
    assert client.post("/api/pair/verify", json={"token": expired}).status_code == 403


@pytestmark_http
@pytest.mark.parametrize(
    "kwargs",
    [
        {"content": b"not json"},
        {"content": b"[1,2]"},
        {"json": {}},
        {"json": {"token": 12345}},
        {"json": {"token": "   "}},
    ],
)
def test_pair_verify_malformed_body_is_400(make_client, kwargs):
    client = make_client()
    resp = client.post("/api/pair/verify", **kwargs)
    assert resp.status_code == 400


@pytestmark_http
def test_pair_verify_body_is_size_limited(make_client):
    client = make_client()
    resp = client.post("/api/pair/verify", json={"token": "x" * 5000})
    assert resp.status_code == 413


@pytestmark_http
def test_pair_verify_locks_out_a_guessing_peer(make_client):
    client = make_client()
    t = client.app.state.agent.t
    for i in range(5):
        assert client.post("/api/pair/verify", json={"token": f"OC-{i:08d}"}).status_code == 403
    locked = client.post("/api/pair/verify", json={"token": "OC-99999999"})
    assert locked.status_code == 429
    assert "Retry-After" in locked.headers
    # Even a genuine token is refused while locked out (no oracle for guessers).
    good = t.create_openclaw_token(ttl=600)
    assert client.post("/api/pair/verify", json={"token": good}).status_code == 429
    # A different peer is unaffected.
    other = make_client(client=("198.51.100.7", 4444))
    assert other.post("/api/pair/verify", json={"token": good}).status_code == 200


@pytestmark_http
def test_openclaw_token_redemption_is_atomic(make_client):
    client = make_client()
    t = client.app.state.agent.t
    token = t.create_openclaw_token(ttl=600)
    assert t.verify_openclaw_token(token) is True
    assert t.verify_openclaw_token(token) is False
    assert t.verify_openclaw_token("OC-NOPE") is False


def test_gateway_pair_qr_refused_on_open_telegram_policy(tmp_path, monkeypatch):
    from solvent import treasury
    from solvent.agent import Solvent
    from solvent.gateway import Gateway

    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    monkeypatch.setattr(treasury, "DB_PATH", tmp_path / "solvent.db")
    gw = Gateway(agent=Solvent(seed_cents=10_000, fresh=True, sync_payment=False))
    session = {"id": "s"}

    monkeypatch.setenv("SOLVENT_TELEGRAM_DM_POLICY", "open")
    refused = gw._handle_command("telegram", "stranger", "/pair qr", session)
    assert "Pairing token" not in refused and "not available" in refused

    # The dashboard chat (dashboard-token gated) and a paired Telegram user still work.
    assert "Pairing token: OC-" in gw._handle_command("dashboard", "d", "/pair qr", session)
    monkeypatch.setenv("SOLVENT_TELEGRAM_DM_POLICY", "pairing")
    assert "Pairing token: OC-" in gw._handle_command("telegram", "u1", "/pair qr", session)


# --- POST /jobs (public intake) -----------------------------------------------


@pytestmark_http
def test_public_job_response_is_sanitized(make_client):
    client = make_client()
    resp = client.post("/jobs", json=_job(id="J-pub"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == "J-pub" and body["stage"] == "invoice"
    assert body["url"] and body["checkout_url"] == body["url"]
    assert body["simulated"] is True
    text = resp.text
    for leaked in ("customer_email", "buyer@example.test", "est_cost", "margin", "_quote"):
        assert leaked not in text


@pytestmark_http
def test_public_job_generates_an_id_when_none_given(make_client):
    client = make_client()
    body = client.post("/jobs", json=_job()).json()
    assert body["job_id"].startswith("J") and len(body["job_id"]) == 9


@pytestmark_http
def test_public_job_cannot_set_internal_fields(make_client):
    client = make_client()
    t = client.app.state.agent.t
    first = client.post("/jobs", json=_job(id="J-1", intake_approved=True, job_owner_session_id="victim"))
    assert first.status_code == 200
    stored = t.get_job("J-1")
    assert "intake_approved" not in (stored.get("job_payload_json") or "")
    assert stored.get("job_owner_session_id") in (None, "")
    # intake_approved used to skip the duplicate screen; now the duplicate is caught.
    dup = client.post("/jobs", json=_job(id="J-2", intake_approved=True))
    assert dup.status_code == 200
    assert dup.json()["stage"] == "declined"


@pytestmark_http
def test_public_decline_does_not_leak_internal_reasons(make_client):
    client = make_client()
    client.post("/jobs", json=_job(id="J-first"))
    dup = client.post("/jobs", json=_job(id="J-second")).json()
    assert dup["stage"] == "declined"
    assert "J-first" not in str(dup) and "intake" not in dup["reason"]

    # Pricing declines must not reveal the margin policy or treasury numbers.
    lowball = client.post(
        "/jobs", json=_job(id="J-low", customer_email="other@example.test", budget_cents=1200, topic="x")
    ).json()
    assert lowball["stage"] == "declined"
    assert "margin" not in lowball["reason"].lower() and "floor" not in lowball["reason"].lower()
    assert set(lowball) == {"stage", "job_id", "reason"}


@pytestmark_http
def test_public_job_cannot_overwrite_an_existing_job_id(make_client):
    client = make_client()
    assert client.post("/jobs", json=_job(id="J-own")).status_code == 200
    clash = client.post("/jobs", json=_job(id="J-own", customer_email="evil@example.test", topic="other"))
    assert clash.status_code == 409
    row = client.app.state.agent.t.get_job("J-own")
    assert row["customer_email"] == "buyer@example.test"


@pytestmark_http
@pytest.mark.parametrize("bad_id", ["../etc/passwd", "a b", "x" * 65, "j;drop", "id\n"])
def test_public_job_rejects_bad_ids(make_client, bad_id):
    client = make_client()
    assert client.post("/jobs", json=_job(id=bad_id)).status_code == 422


@pytestmark_http
def test_public_job_input_limits(make_client):
    client = make_client()
    assert client.post("/jobs", content=b"{nope").status_code == 400
    assert client.post("/jobs", content=b"[]").status_code == 422
    assert client.post("/jobs", json=_job(budget_cents="lots")).status_code == 422
    assert client.post("/jobs", json=_job(context="x" * 5000)).status_code == 422
    assert client.post("/jobs", json=_job(topic="x" * 40_000)).status_code == 413
    # Oversize with no Content-Length (chunked) is cut off while streaming.
    def chunks():
        for _ in range(100):
            yield b"x" * 1024

    assert client.post("/jobs", content=chunks()).status_code == 413


@pytestmark_http
def test_dashboard_job_route_keeps_full_detail(make_client):
    client = make_client()
    assert client.post("/api/job", json=_job(id="J-dash")).status_code == 403
    resp = client.post("/api/job", json=_job(id="J-dash"), headers=AUTH)
    assert resp.status_code == 200
    assert "amount_cents" in resp.json() and "session_id" in resp.json()


# --- GET /jobs/{id} -----------------------------------------------------------


@pytestmark_http
def test_job_status_is_minimal_without_auth(make_client):
    client = make_client()
    client.post("/jobs", json=_job(id="J-view"))
    resp = client.get("/jobs/J-view")
    assert resp.status_code == 200
    assert resp.json() == {"job": {"id": "J-view", "status": "awaiting_payment"}}
    assert "buyer@example.test" not in resp.text and "customer_email" not in resp.text
    assert "metrics" not in resp.json()
    # A wrong token is the same as no token.
    assert client.get("/jobs/J-view", headers={"X-Solvent-Dashboard-Token": "x"}).json() == resp.json()


@pytestmark_http
def test_job_status_cancel_redirect_still_works(make_client):
    client = make_client()
    client.post("/jobs", json=_job(id="J-cancel"))
    body = client.get("/jobs/J-cancel?cancelled=1").json()
    assert body["cancelled"] is True and body["job"]["status"] == "awaiting_payment"


@pytestmark_http
def test_job_status_full_row_with_dashboard_token(make_client):
    client = make_client()
    client.post("/jobs", json=_job(id="J-full"))
    body = client.get("/jobs/J-full", headers=AUTH).json()
    assert body["job"]["customer_email"] == "buyer@example.test"
    assert "metrics" in body
    assert client.get("/jobs/J-nope").status_code == 404


# --- local-only routes behind a proxy ------------------------------------------


@pytest.fixture
def brief(tmp_path):
    from solvent.paths import reports_dir

    d = reports_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / "proxy-job.md").write_text("# Private brief", encoding="utf-8")
    return "proxy-job"


@pytestmark_http
def test_local_routes_work_locally(make_client, brief):
    client = make_client()
    assert client.get("/api/briefs").status_code == 200
    assert client.get(f"/api/briefs/{brief}").status_code == 200


@pytestmark_http
def test_proxy_headers_close_the_local_only_routes(make_client, brief):
    from solvent.delivery import make_delivery_token

    client = make_client()  # peer looks like loopback ("testclient")
    for headers in (PROXY, {"Forwarded": "for=203.0.113.50"}, {"X-Real-IP": "203.0.113.50"}):
        assert client.get("/api/briefs", headers=headers).status_code == 403
        assert client.get(f"/api/briefs/{brief}", headers=headers).status_code == 403
        assert client.get("/api/receipt/anything", headers=headers).status_code == 404  # no such job: not a leak
    # A valid delivery token still works through the proxy.
    token = make_delivery_token(brief)
    assert client.get(f"/api/briefs/{brief}?token={token}", headers=PROXY).status_code == 200


@pytestmark_http
def test_receipt_is_not_open_through_a_proxy(make_client):
    client = make_client()
    client.post("/jobs", json=_job(id="J-rcpt"))
    assert client.get("/api/receipt/J-rcpt").status_code == 200
    assert client.get("/api/receipt/J-rcpt", headers=PROXY).status_code == 403


@pytestmark_http
def test_local_access_env_override_on_the_server(make_client, brief, monkeypatch):
    client = make_client()
    monkeypatch.setenv("SOLVENT_LOCAL_ACCESS", "never")
    assert client.get("/api/briefs").status_code == 403
    monkeypatch.setenv("SOLVENT_LOCAL_ACCESS", "peer")
    assert client.get("/api/briefs", headers=PROXY).status_code == 200
    monkeypatch.setenv("SOLVENT_LOCAL_ACCESS", "auto")
    assert client.get("/api/briefs", headers=PROXY).status_code == 403


@pytestmark_http
def test_remote_peer_is_never_local(make_client, brief):
    client = make_client(client=("203.0.113.10", 5555))
    assert client.get("/api/briefs").status_code == 403


# --- every route is accounted for ------------------------------------------------

#: Routes intentionally reachable without the dashboard token, and why.
PUBLIC_ROUTES = {
    ("GET", "/health"): "liveness probe; balance only with the token",
    ("POST", "/api/pair/verify"): "single-use pairing token is the credential; attempt-limited",
    ("POST", "/jobs"): "public intake; input-limited, whitelisted fields, sanitized reply",
    ("GET", "/jobs/{job_id}"): "id + status only without the token",
    ("GET", "/api/receipt/{job_id}"): "delivery token or genuinely local",
    ("POST", "/webhooks/stripe"): "Stripe signature verified first",
    ("GET", "/briefs/{job_id}"): "delivery token",
    ("GET", "/api/briefs"): "genuinely local only",
    ("GET", "/api/briefs/{job_id}"): "delivery token or genuinely local",
}


@pytestmark_http
def test_every_other_route_requires_the_dashboard_token(make_client):
    client = make_client()
    seen = set()
    for route in client.app.routes:
        methods = getattr(route, "methods", None) or set()
        path = getattr(route, "path", "")
        if not path or path in ("/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"):
            continue
        for method in methods - {"HEAD", "OPTIONS"}:
            key = (method, path)
            seen.add(key)
            if key in PUBLIC_ROUTES:
                continue
            url = path.replace("{job_id}", "J1").replace("{event_id}", "evt_1")
            if method == "GET" and path == "/api/events":
                # SSE endpoint: assert the auth failure without opening the stream.
                assert client.get(url).status_code == 403
                continue
            resp = client.request(method, url, json={"message": "x"} if method == "POST" else None)
            assert resp.status_code in (401, 403), f"{method} {path} is reachable without auth"
    assert set(PUBLIC_ROUTES) <= seen, "PUBLIC_ROUTES lists a route that no longer exists"

