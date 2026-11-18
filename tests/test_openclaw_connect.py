"""Connect-screen values, exposure warnings, and pairing-token revocation."""

import json
from unittest.mock import patch

import pytest

from solvent import openclaw
from solvent.treasury import Treasury


@pytest.fixture
def treasury(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    from solvent import treasury as treasury_mod

    monkeypatch.setattr(treasury_mod, "DB_PATH", tmp_path / "solvent.db")
    return Treasury()


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://agent.example.com", ("agent.example.com", 443, True)),
        ("http://203.0.113.5:8787", ("203.0.113.5", 8787, False)),
        ("https://agent.example.com:8443/x", ("agent.example.com", 8443, True)),
        ("agent.example.com", ("agent.example.com", 80, False)),
        ("http://host:notaport", ("host", 80, False)),
        ("", ("", 80, False)),
    ],
)
def test_parse_endpoint(url, expected):
    assert openclaw.parse_endpoint(url) == expected


def test_connect_info_builds_the_discovery_url():
    info = openclaw.connect_info(
        {"SOLVENT_BASE_URL": "https://agent.example.com", "SOLVENT_GATEWAY_PASSWORD": "pw"}
    )
    assert info["discovery_url"] == "https://agent.example.com/.well-known/solvent.json"
    assert info["gateway_password_set"] is True
    assert info["tls"] is True
    custom = openclaw.connect_info({"SOLVENT_BASE_URL": "http://203.0.113.5:8787"})
    assert custom["discovery_url"] == "http://203.0.113.5:8787/.well-known/solvent.json"


def test_loopback_has_no_exposure_warnings():
    assert openclaw.exposure_warnings({"SOLVENT_BASE_URL": "http://127.0.0.1:8787"}) == []
    assert openclaw.exposure_warnings({}) == []


def test_public_host_is_flagged_until_hardened():
    env = {"SOLVENT_BASE_URL": "http://203.0.113.5:8787"}
    assert len(openclaw.exposure_warnings(env)) == 3
    hardened = {
        "SOLVENT_BASE_URL": "https://agent.example.com",
        "SOLVENT_GATEWAY_PASSWORD": "pw",
        "SOLVENT_DASHBOARD_TOKEN": "tok",
    }
    assert openclaw.exposure_warnings(hardened) == []


def test_doctor_reports_exposure(monkeypatch, tmp_path):
    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    monkeypatch.setenv("SOLVENT_BASE_URL", "http://203.0.113.5:8787")
    monkeypatch.delenv("SOLVENT_GATEWAY_PASSWORD", raising=False)
    from solvent.doctor import run_checks

    check = next(c for c in run_checks() if c["name"] == "openclaw_exposure")
    assert check["ok"] is False
    assert "SOLVENT_GATEWAY_PASSWORD" in check["detail"]


def test_list_masks_tokens_and_revoke_invalidates_them(treasury):
    first = treasury.create_openclaw_token()
    second = treasury.create_openclaw_token()
    used = treasury.create_openclaw_token()
    assert treasury.verify_openclaw_token(used)

    listed = treasury.list_openclaw_tokens()
    assert len(listed) == 2
    assert all(first not in json.dumps(t) and second not in json.dumps(t) for t in listed)

    assert treasury.revoke_openclaw_tokens() == 2
    assert treasury.list_openclaw_tokens() == []
    assert not treasury.verify_openclaw_token(first)
    assert not treasury.verify_openclaw_token(second)
    assert treasury.revoke_openclaw_tokens() == 0


def test_expired_tokens_are_not_listed_or_counted(treasury):
    treasury.create_openclaw_token(ttl=-1)
    assert treasury.list_openclaw_tokens() == []
    assert treasury.revoke_openclaw_tokens() == 0


def test_connect_cli_json_and_revoke(treasury, monkeypatch, capsys):
    monkeypatch.setenv("SOLVENT_BASE_URL", "https://agent.example.com")
    with patch("sys.argv", ["solvent connect", "--json"]):
        openclaw.main()
    out = json.loads(capsys.readouterr().out)
    assert out["host"] == "agent.example.com" and out["token"].startswith("OC-")
    assert len(treasury.list_openclaw_tokens()) == 1

    with patch("sys.argv", ["solvent connect", "--revoke"]):
        openclaw.main()
    assert "Revoked 1" in capsys.readouterr().out
    assert treasury.list_openclaw_tokens() == []


def test_connect_cli_no_token(treasury, monkeypatch, capsys):
    monkeypatch.setenv("SOLVENT_BASE_URL", "https://agent.example.com")
    with patch("sys.argv", ["solvent connect", "--no-token", "--json"]):
        openclaw.main()
    assert json.loads(capsys.readouterr().out)["token"] is None
    assert treasury.list_openclaw_tokens() == []


def test_pair_tokens_routes_need_the_dashboard_token(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from solvent import server
    from solvent import treasury as treasury_mod
    from solvent.webhook_log import WebhookLog

    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    monkeypatch.setenv("SOLVENT_DASHBOARD_TOKEN", "d" * 32)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setattr(treasury_mod, "DB_PATH", tmp_path / "solvent.db")
    monkeypatch.setattr(server, "WebhookLog", lambda: WebhookLog(":memory:"))
    client = TestClient(server.create_app(fresh=True))
    auth = {"X-Solvent-Dashboard-Token": "d" * 32}

    assert client.get("/api/pair/tokens").status_code in (401, 403)
    assert client.delete("/api/pair/tokens").status_code in (401, 403)

    token = client.app.state.agent.t.create_openclaw_token()
    listed = client.get("/api/pair/tokens", headers=auth).json()["tokens"]
    assert len(listed) == 1 and token not in json.dumps(listed)
    assert client.delete("/api/pair/tokens", headers=auth).json() == {"revoked": 1}
    assert client.post("/api/pair/verify", json={"token": token}).status_code == 403
