"""The gateway password (``SOLVENT_GATEWAY_PASSWORD``) on POST /api/pair/verify.

The password is checked before the pairing token. Unlike a 128-bit token it may
be weak, so wrong guesses are attempt-limited, and a blocked peer is refused
without the password being compared at all.
"""

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:
    pytest.skip("FastAPI test client is not installed", allow_module_level=True)

from solvent import server, treasury
from solvent.webhook_log import WebhookLog

PASSWORD = "correct-horse-battery"
VERIFY = "/api/pair/verify"


@pytest.fixture
def make_app(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    monkeypatch.setenv("SOLVENT_DASHBOARD_TOKEN", "d" * 32)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setattr(treasury, "DB_PATH", tmp_path / "solvent.db")
    monkeypatch.setattr(server, "WebhookLog", lambda: WebhookLog(":memory:"))

    def build(password: str | None = PASSWORD):
        if password is None:
            monkeypatch.delenv("SOLVENT_GATEWAY_PASSWORD", raising=False)
        else:
            monkeypatch.setenv("SOLVENT_GATEWAY_PASSWORD", password)
        return server.create_app(fresh=True)

    return build


def _verify(client, token="not-a-token", password=None, header="Authorization"):
    headers = {}
    if password is not None:
        headers[header] = f"Bearer {password}" if header == "Authorization" else password
    return client.post(VERIFY, json={"token": token}, headers=headers)


def test_unset_password_leaves_pairing_unchanged(make_app):
    client = TestClient(make_app(password=None))
    assert _verify(client).status_code == 403  # bad token, no password demanded
    token = client.app.state.agent.t.create_openclaw_token()
    assert _verify(client, token=token).json() == {"verified": True}


@pytest.mark.parametrize("header", ["Authorization", "X-Solvent-Gateway-Password"])
def test_correct_password_reaches_the_token_check(make_app, header):
    client = TestClient(make_app())
    assert _verify(client, password=PASSWORD, header=header).status_code == 403
    token = client.app.state.agent.t.create_openclaw_token()
    response = _verify(client, token=token, password=PASSWORD, header=header)
    assert response.json() == {"verified": True}


def test_missing_or_wrong_password_is_401(make_app):
    client = TestClient(make_app())
    assert _verify(client).status_code == 401
    assert _verify(client, password="wrong").status_code == 401


def test_repeated_wrong_passwords_are_blocked_without_an_oracle(make_app):
    client = TestClient(make_app())
    codes = [_verify(client, password=f"guess-{i}").status_code for i in range(5)]
    assert codes == [401] * 5

    # Blocked: even the right password (and a valid token) is refused, so a
    # guesser cannot tell when a guess was correct.
    token = client.app.state.agent.t.create_openclaw_token()
    blocked = _verify(client, token=token, password=PASSWORD)
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"] == "300"


def test_one_peer_being_blocked_does_not_block_another(make_app):
    app = make_app()
    attacker = TestClient(app, client=("203.0.113.10", 50000))
    for i in range(5):
        _verify(attacker, password=f"guess-{i}")
    assert _verify(attacker, password=PASSWORD).status_code == 429

    owner = TestClient(app, client=("198.51.100.7", 50001))
    token = app.state.agent.t.create_openclaw_token()
    assert _verify(owner, token=token, password=PASSWORD).json() == {"verified": True}
