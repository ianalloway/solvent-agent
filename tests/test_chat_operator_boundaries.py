from unittest.mock import patch

import pytest

from solvent import gateway
from solvent.agent import Solvent
from solvent.gateway import Gateway
from solvent.rate_limit import RateLimiter


@pytest.fixture
def gw(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    monkeypatch.setenv("SOLVENT_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("SOLVENT_OPERATOR_IDS", "100")
    monkeypatch.setenv("SOLVENT_TELEGRAM_DM_POLICY", "pairing")
    monkeypatch.setattr(gateway, "_rate_limiter", RateLimiter(db_path=":memory:", burst_limit=100))
    return Gateway(agent=Solvent(seed_cents=10000, fresh=False, sync_payment=False))


@pytest.mark.parametrize(
    "user,chat,kind,allowed",
    [
        ("200", "200", "private", False),
        ("200", "-10", "group", False),
        ("100", "-10", "supergroup", False),
        ("100", None, None, False),
        ("100", "100", "private", True),
    ],
)
@pytest.mark.parametrize("command", ["/status", "/pair qr"])
def test_operator_command_boundaries(gw, user, chat, kind, allowed, command):
    with (
        patch("solvent.pairing.is_allowed", return_value=True),
        patch.object(
            gw.agent.t,
            "snapshot",
            return_value={"balance_cents": 987654, "revenue_cents": 123456, "margin_pct": 42},
        ) as snapshot,
        patch.object(gw.agent.t, "create_openclaw_token", return_value="TEST") as mint,
        patch("solvent.qr.render_token", return_value="TEST_QR"),
    ):
        reply = gw.handle_inbound("telegram", user, command, chat_id=chat, chat_type=kind)
    assert (snapshot.called if command == "/status" else mint.called) == allowed
    if not allowed:
        assert "operator-only" in reply


@pytest.mark.parametrize(
    "channel,operator,allowed",
    [
        ("telegram", False, False),
        ("telegram", True, True),
        ("cli", False, True),
        ("dashboard", False, True),
        ("unknown", False, False),
    ],
)
def test_model_cannot_bypass_treasury_authorization(gw, channel, operator, allowed):
    from solvent.chat import handle_message

    session = gw.memory.get_or_create(channel, "test")
    with (
        patch.object(
            gw.agent.t,
            "snapshot",
            return_value={
                "balance_cents": 987654,
                "revenue_cents": 123456,
                "expense_cents": 100,
                "margin_pct": 42,
            },
        ) as snapshot,
        patch(
            "solvent.nemotron.complete",
            side_effect=[
                ('<tool_call>{"name":"treasury_status","arguments":{}}</tool_call>', {}),
                ("done", {}),
            ],
        ) as model,
    ):
        handle_message(
            session["id"],
            "hello",
            agent=gw.agent,
            memory=gw.memory,
            channel=channel,
            telegram_operator=operator,
        )
    assert snapshot.called == allowed
    assert ("987654" in model.call_args.args[1]) == allowed
