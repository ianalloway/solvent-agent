"""Telegram prompt and history authorization regressions (no live model/API)."""

import json
import os
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from solvent import gateway, workspace
from solvent.agent import Solvent
from solvent.channels import telegram
from solvent.gateway import Gateway
from solvent.rate_limit import RateLimiter
from solvent.treasury import Treasury


@pytest.fixture
def isolated_gateway(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLVENT_HOME", str(tmp_path))
    monkeypatch.setenv("SOLVENT_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("SOLVENT_TELEGRAM_DM_POLICY", "open")
    monkeypatch.setenv("SOLVENT_OPERATOR_IDS", "100")
    monkeypatch.setattr(gateway, "_rate_limiter", RateLimiter(db_path=":memory:", burst_limit=50))
    root = workspace.seed_workspace()
    for name in ("MEMORY.md", "BRAIN.md"):
        (root / name).write_text(f"PRIVATE_{name}")
    (root / "memory" / f"{date.today()}.md").write_text("PRIVATE_DAILY")
    agent = Solvent(seed_cents=10_000, fresh=False, sync_payment=False)
    agent.t = Treasury(path=tmp_path / "t.db")
    return Gateway(agent=agent)


def assert_private(prompt, allowed):
    for marker in ("PRIVATE_MEMORY.md", "PRIVATE_BRAIN.md", "PRIVATE_DAILY"):
        assert (marker in prompt) == allowed


def test_telegram_channel_alone_does_not_authorize_private_memory(isolated_gateway):
    assert_private(workspace.build_chat_system_prompt("telegram"), False)
    assert_private(workspace.build_chat_system_prompt("cli"), True)
    assert_private(workspace.build_system_prompt(session_kind="shared"), False)


@pytest.mark.parametrize("text", ["hello", "/quote chips | 50"])
@pytest.mark.parametrize(
    "user_id,chat_id,chat_type,allowed",
    [("100", "100", "private", True), ("200", "200", "private", False),
     ("100", "-10", "group", False), ("100", "-10", "supergroup", False),
     ("200", "-10", "group", False), ("100", None, None, False)],
)
def test_prompt_authorization(isolated_gateway, text, user_id, chat_id, chat_type, allowed):
    with patch("solvent.nemotron.complete", return_value=("ok", {})) as model:
        isolated_gateway.handle_inbound(
            "telegram", user_id, text, chat_id=chat_id, chat_type=chat_type,
            user_label="100",  # A username is not an operator identity.
        )
    assert_private(model.call_args.args[0], allowed)


def test_histories_and_pending_commissions_are_isolated(isolated_gateway):
    gw = isolated_gateway
    old = gw.memory.get_or_create("telegram", "200")
    gw.memory.append(old["id"], "assistant", "LEGACY_PRIVATE_CONTEXT")
    contexts = [("100", "100", "private"), ("200", "200", "private"),
                ("300", "300", "private"), ("200", "-10", "group"),
                ("300", "-10", "group"), ("200", "-20", "supergroup")]
    with patch("solvent.nemotron.complete", return_value=("ok", {})) as model:
        for index, (user, chat, kind) in enumerate(contexts):
            gw.handle_inbound("telegram", user, f"Commission a brief topic: MARKER_{index}",
                              chat_id=chat, chat_type=kind)
        for index, (user, chat, kind) in enumerate(contexts):
            gw.handle_inbound("telegram", user, "continue", chat_id=chat, chat_type=kind)
            prompt = model.call_args.args[1]
            assert f"MARKER_{index}" in prompt
            assert "LEGACY_PRIVATE_CONTEXT" not in prompt
            for other in range(len(contexts)):
                if other != index:
                    assert f"MARKER_{other}" not in prompt
    sessions = [s for s in gw.agent.t.list_chat_sessions_by_channel("telegram")
                if s["id"] != old["id"]]
    assert len(sessions) == len(contexts)
    assert len({s["pending_job_json"] for s in sessions}) == len(contexts)


def test_operator_revocation_cannot_reuse_private_history(isolated_gateway, monkeypatch):
    gw = isolated_gateway
    with patch("solvent.nemotron.complete", return_value=("PRIVATE_REPLY", {})) as model:
        gw.handle_inbound("telegram", "100", "hello", chat_id="100", chat_type="private")
        monkeypatch.setenv("SOLVENT_OPERATOR_IDS", "")
        gw.handle_inbound("telegram", "100", "hello again", chat_id="100", chat_type="private")
        assert_private(model.call_args.args[0], False)
        assert "PRIVATE_REPLY" not in model.call_args.args[1]


def test_operator_commands_are_not_exposed_to_groups(isolated_gateway):
    reply = isolated_gateway.handle_inbound(
        "telegram", "100", "/customers", chat_id="-10", chat_type="group"
    )
    assert "operator-only" in reply


def test_pairing_checks_user_not_conversation_key(isolated_gateway, monkeypatch):
    monkeypatch.setenv("SOLVENT_TELEGRAM_DM_POLICY", "pairing")
    with patch("solvent.pairing.is_allowed", return_value=False) as allowed, patch(
        "solvent.nemotron.complete"
    ) as model:
        reply = isolated_gateway.handle_inbound(
            "telegram", "200", "hello", chat_id="-10", chat_type="group"
        )
        assert "Pairing required" in reply
        allowed.assert_called_once_with("200", None)
        model.assert_not_called()


def test_job_notifications_use_conversation_destination(isolated_gateway, monkeypatch):
    gw = isolated_gateway
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    with patch("solvent.nemotron.complete", return_value=("ok", {})):
        gw.handle_inbound("telegram", "200", "hello", chat_id="-10", chat_type="group")
    session = gw.agent.t.list_chat_sessions_by_channel("telegram")[0]
    gw.agent.t.update_chat_session(session["id"], notify_job_id="J1")
    monkeypatch.setitem(gateway._outbound_handlers, "telegram", telegram.send_telegram_message)
    with patch("urllib.request.urlopen") as send:
        gw.on_job_event({"stage": "receipt_ready", "job_id": "J1", "receipt": "receipt"})
        payload = json.loads(send.call_args.args[0].data)
        assert payload["chat_id"] == -10
        telegram.send_telegram_message("200", "legacy notification")
        assert json.loads(send.call_args.args[0].data)["chat_id"] == 200


def test_adapter_passes_authenticated_user_and_chat():
    import asyncio

    app = MagicMock()
    application = MagicMock()
    application.builder.return_value.token.return_value.build.return_value = app
    def command_handler(command, callback):
        return SimpleNamespace(callback=callback)
    gw = MagicMock()
    gw.handle_inbound.return_value = "ok"
    with patch.object(telegram, "_require_ptb", return_value=(
        application, command_handler, MagicMock(), MagicMock()
    )), patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test-token"}), patch.object(
        telegram, "register_outbound"
    ):
        telegram.build_application(gw)
    callback = app.add_handler.call_args_list[0].args[0].callback
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=200, username="customer"),
        effective_chat=SimpleNamespace(id=-10, type="supergroup"),
        message=SimpleNamespace(text="hello", reply_text=AsyncMock()),
    )
    asyncio.run(callback(update, None))
    gw.handle_inbound.assert_called_once_with(
        "telegram", "200", "hello", user_label="customer", chat_id="-10", chat_type="supergroup"
    )
