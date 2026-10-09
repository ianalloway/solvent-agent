import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from solvent import gateway
from solvent.agent import Solvent
from solvent.gateway import Gateway, notify, register_outbound
from solvent.rate_limit import RateLimiter
from solvent.treasury import Treasury


class TestGateway(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        self.t = Treasury(path=self.db)
        self.t.reset()
        self.t.seed(10_000)
        self.agent = Solvent(seed_cents=10_000, fresh=False, sync_payment=False)
        self.agent.t = self.t
        self.gw = Gateway(agent=self.agent)
        self._limiter_patch = patch.object(
            gateway, "_rate_limiter", RateLimiter(db_path=":memory:")
        )
        self._limiter_patch.start()

    def tearDown(self):
        self._limiter_patch.stop()
        self.tmp.cleanup()

    @patch.dict(os.environ, {"SOLVENT_TELEGRAM_DM_POLICY": "open"})
    def test_handle_inbound_status_command(self):
        reply = self.gw.handle_inbound("telegram", "12345", "/status")
        self.assertIn("Balance", reply)

    @patch.dict(os.environ, {"SOLVENT_TELEGRAM_DM_POLICY": "pairing"})
    def test_pairing_required(self):
        reply = self.gw.handle_inbound("telegram", "pairing-user-1", "hello")
        self.assertIn("Pairing", reply)

    @patch.dict(os.environ, {"SOLVENT_TELEGRAM_DM_POLICY": "pairing"})
    def test_start_returns_code(self):
        reply = self.gw.handle_inbound("telegram", "pairing-user-2", "/start")
        self.assertIn("TG-", reply)

    def test_banned_user_is_refused_even_for_commands(self):
        gateway._rate_limiter.ban("cli:banned-user", 3600, "abuse")
        with patch("solvent.gateway.handle_message") as model:
            for text in ("/status", "/help", "/quote topic | 50", "/unknown", "hello"):
                reply = self.gw.handle_inbound("cli", "banned-user", text)
                self.assertIn("blocked", reply)
                self.assertNotIn("Balance", reply)
            model.assert_not_called()

    def test_unknown_slash_command_never_reaches_the_model(self):
        with patch("solvent.gateway.handle_message") as model:
            reply = self.gw.handle_inbound("cli", "u1", "/write me a 5000 word report")
            self.assertIn("Unknown command", reply)
            reply = self.gw.handle_inbound("cli", "u1", "/quote no budget given")
            self.assertIn("Usage", reply)
            model.assert_not_called()

    def test_quote_counts_against_the_rate_limit_but_cheap_commands_do_not(self):
        limiter = RateLimiter(db_path=":memory:", burst_limit=2)
        with patch.object(gateway, "_rate_limiter", limiter), patch(
            "solvent.gateway.handle_message", return_value="quoted"
        ) as model:
            replies = [self.gw.handle_inbound("cli", "u2", "/quote chips | 50") for _ in range(4)]
            self.assertEqual(replies[:2], ["quoted", "quoted"])
            self.assertTrue(all("Rate limit" in r for r in replies[2:]))
            self.assertEqual(model.call_count, 2)

            for _ in range(5):
                self.assertIn("Balance", self.gw.handle_inbound("cli", "u2", "/status"))

    def test_notify_outbound(self):
        sent = []

        def handler(ext, text):
            sent.append((ext, text))

        register_outbound("testchan", handler)
        notify("testchan", "42", "hello")
        self.assertEqual(sent, [("42", "hello")])

    def test_notify_only_enqueues_dashboard_notifications(self):
        with patch("solvent.notifications.enqueue_chat") as enqueue_chat:
            notify("telegram", "42", "private")
            enqueue_chat.assert_not_called()

            notify("dashboard", "web-1", "hello")
            enqueue_chat.assert_called_once_with("dashboard", "web-1", "hello")
