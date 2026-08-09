import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from solvent import pairing
from solvent.treasury import Treasury


class TestTelegramPairing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "t.db"
        self.t = Treasury(path=self.db)
        self.t.reset()
        pairing.ALLOWLIST_PATH = Path(self.tmp.name) / "allowlist.json"
        self._treasury_patch = patch("solvent.pairing.Treasury", return_value=self.t)
        self._treasury_patch.start()

    def tearDown(self):
        self._treasury_patch.stop()
        self.tmp.cleanup()

    def test_pairing_flow(self):
        code = pairing.request_pairing("user-1", "alice")
        self.assertTrue(code.startswith("TG-"))
        self.assertFalse(pairing.is_allowed("user-1", "alice"))
        pending = pairing.list_pending()
        self.assertEqual(len(pending), 1)
        row = pairing.approve(code)
        self.assertIsNotNone(row)
        self.assertTrue(pairing.is_allowed("user-1", "alice"))

    def test_allowlist_policy(self):
        os.environ["SOLVENT_TELEGRAM_DM_POLICY"] = "allowlist"
        os.environ["SOLVENT_TELEGRAM_ALLOW_FROM"] = "user-2"
        try:
            self.assertTrue(pairing.is_allowed("user-2"))
            self.assertFalse(pairing.is_allowed("user-3"))
        finally:
            os.environ.pop("SOLVENT_TELEGRAM_DM_POLICY", None)
            os.environ.pop("SOLVENT_TELEGRAM_ALLOW_FROM", None)

    def test_dm_policy_defaults_to_pairing(self):
        os.environ.pop("SOLVENT_TELEGRAM_DM_POLICY", None)
        try:
            self.assertEqual(pairing.dm_policy(), "pairing")
        finally:
            os.environ.pop("SOLVENT_TELEGRAM_DM_POLICY", None)

    def test_dm_policy_reads_env(self):
        os.environ["SOLVENT_TELEGRAM_DM_POLICY"] = "open"
        try:
            self.assertEqual(pairing.dm_policy(), "open")
        finally:
            os.environ.pop("SOLVENT_TELEGRAM_DM_POLICY", None)

    def test_is_allowed_open_policy(self):
        os.environ["SOLVENT_TELEGRAM_DM_POLICY"] = "open"
        try:
            self.assertTrue(pairing.is_allowed("any-user"))
            self.assertTrue(pairing.is_allowed("any-user", "anyone"))
        finally:
            os.environ.pop("SOLVENT_TELEGRAM_DM_POLICY", None)

    def test_is_allowed_disabled_policy(self):
        os.environ["SOLVENT_TELEGRAM_DM_POLICY"] = "disabled"
        try:
            self.assertFalse(pairing.is_allowed("any-user"))
            self.assertFalse(pairing.is_allowed("any-user", "anyone"))
        finally:
            os.environ.pop("SOLVENT_TELEGRAM_DM_POLICY", None)
