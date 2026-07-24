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
        self._limiter_patch = patch.object(gateway, "_rate_limiter", RateLimiter(db_path=":memory:"))
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

    def test_notify_outbound(self):
        sent = []

        def handler(ext, text):
            sent.append((ext, text))

        register_outbound("testchan", handler)
        notify("testchan", "42", "hello")
        self.assertEqual(sent, [("42", "hello")])
