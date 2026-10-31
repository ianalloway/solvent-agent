"""Tests for Nano custody M3 (issue #83). Sim-only: no real keys, no network."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from solvent.nano_custody import (
    NanoCustody,
    NanoCustodyError,
    SecretHandle,
    parse_key_ref,
    resolve_key_ref,
)
from solvent.paths import config_path

# Syntactically valid but unowned test addresses (no keys exist for them here).
HOT = "nano_1qato4k7z3spc8gq1zyd8xeqfbzsoxwo36a45ozbrxcatut7up8ohyardu1z"
COLD = "nano_3t6k35gi95xu6tergt6p69ck76ogmitsa8mnijtpxm9fkcm736xtoncuohr3"
XNO = 10**30


class _Env(unittest.TestCase):
    base: dict = {}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        env = {
            "SOLVENT_HOME": self._tmp.name,
            "SOLVENT_NANO": "",
            "SOLVENT_NANO_RPC": "",
            "SOLVENT_NANO_ADDRESS": "",
            "SOLVENT_NANO_CUSTODY": "",
            "SOLVENT_NANO_CUSTODY_KEY_REF": "",
            "SOLVENT_NANO_COLD_ADDRESS": "",
            "SOLVENT_NANO_SWEEP_MIN_RAW": "",
            "SOLVENT_NANO_SWEEP_DAILY_CAP_RAW": "",
            "SOLVENT_FORCE_NANO_SIMULATE": "",
        }
        env.update(self.base)
        self._p = mock.patch.dict(os.environ, env, clear=False)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        self._tmp.cleanup()

    def book(self, blocks: dict[str, int]):
        path = config_path("nano_payments.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({b: {"amount_raw": str(a), "job_id": b} for b, a in blocks.items()})
        )


class TestDefaultOff(_Env):
    def test_off_by_default(self):
        c = NanoCustody.from_env()
        self.assertEqual(c.mode, "off")
        self.assertFalse(c.enabled)
        self.assertFalse(c.plan_sweep().ok)
        with self.assertRaises(NanoCustodyError):
            c.execute_sweep(approve=lambda p: True)

    def test_stray_config_while_off_fails_closed(self):
        os.environ["SOLVENT_NANO_CUSTODY_KEY_REF"] = "env:NANO_KEY"
        self.assertEqual(NanoCustody.from_env().mode, "misconfigured")

    def test_unknown_flag(self):
        os.environ["SOLVENT_NANO_CUSTODY"] = "yolo"
        self.assertEqual(NanoCustody.from_env().mode, "misconfigured")


class TestKeyRefs(_Env):
    def test_env_and_keyring_refs_parse(self):
        self.assertEqual(parse_key_ref("env:NANO_KEY"), ("env", ("NANO_KEY",)))
        self.assertEqual(parse_key_ref("keyring:solvent/nano"), ("keyring", ("solvent", "nano")))

    def test_raw_material_and_files_refused(self):
        for bad in ("A" * 64, "0" * 128, "seed:abc", "file:/tmp/seed", "/etc/key", ""):
            with self.subTest(bad=bad), self.assertRaises(NanoCustodyError):
                parse_key_ref(bad)

    def test_secret_handle_redacts(self):
        os.environ["FAKE_TEST_KEY"] = "not-a-real-key"
        h = resolve_key_ref("env:FAKE_TEST_KEY")
        self.assertIsInstance(h, SecretHandle)
        self.assertNotIn("not-a-real-key", repr(h))
        self.assertNotIn("not-a-real-key", str(h))
        self.assertEqual(h.reveal(), "not-a-real-key")

    def test_empty_env_ref(self):
        with self.assertRaises(NanoCustodyError):
            resolve_key_ref("env:DEFINITELY_UNSET_VAR_X")


class TestSimCustody(_Env):
    base = {"SOLVENT_NANO": "sim", "SOLVENT_NANO_CUSTODY": "sim"}

    def test_requires_sim_rail(self):
        os.environ["SOLVENT_NANO"] = ""
        self.assertEqual(NanoCustody.from_env().mode, "misconfigured")

    def test_sim_never_reads_keys(self):
        os.environ["SOLVENT_NANO_CUSTODY_KEY_REF"] = "env:NANO_KEY"
        self.assertEqual(NanoCustody.from_env().mode, "misconfigured")

    def test_sim_refuses_real_cold_address(self):
        os.environ["SOLVENT_NANO_COLD_ADDRESS"] = COLD
        self.assertEqual(NanoCustody.from_env().mode, "misconfigured")

    def test_balance_counts_only_sim_blocks(self):
        self.book({"block_sim_a": 2 * XNO, "block_sim_b": XNO, "F" * 64: 50 * XNO})
        c = NanoCustody.from_env()
        self.assertEqual(c.mode, "sim")
        self.assertEqual(c.balance_raw(), 3 * XNO)

    def test_sweep_needs_approval(self):
        self.book({"block_sim_a": 2 * XNO})
        c = NanoCustody.from_env()
        with self.assertRaises(NanoCustodyError):
            c.execute_sweep()
        with self.assertRaises(NanoCustodyError):
            c.execute_sweep(approve=lambda p: False)
        self.assertEqual(c.balance_raw(), 2 * XNO)

    def test_sweep_moves_balance_and_persists(self):
        self.book({"block_sim_a": 2 * XNO})
        c = NanoCustody.from_env()
        rec = c.execute_sweep(approve=lambda p: True)
        self.assertTrue(rec["block"].startswith("block_sim_sweep_"))
        self.assertTrue(rec["destination"].startswith("nano_sim_cold_"))
        self.assertEqual(int(rec["amount_raw"]), 2 * XNO)
        again = NanoCustody.from_env()
        self.assertEqual(again.balance_raw(), 0)
        self.assertFalse(again.plan_sweep().ok)

    def test_below_min_refused(self):
        self.book({"block_sim_a": 10})
        self.assertIn("below", NanoCustody.from_env().plan_sweep().reason)

    def test_daily_cap(self):
        os.environ["SOLVENT_NANO_SWEEP_DAILY_CAP_RAW"] = str(XNO)
        self.book({"block_sim_a": 3 * XNO})
        now = [1_000_000.0]
        c = NanoCustody.from_env(clock=lambda: now[0])
        self.assertEqual(int(c.execute_sweep(approve=lambda p: True)["amount_raw"]), XNO)
        self.assertIn("cap", c.plan_sweep().reason)
        now[0] += 24 * 3600 + 1
        self.assertEqual(c.plan_sweep().amount_raw, XNO)

    def test_status_has_no_secrets(self):
        s = NanoCustody.from_env().status()
        self.assertFalse(s["live_validated"])
        self.assertEqual(s["key_ref_kind"], "")


class _FakeSigner:
    def __init__(self):
        self.calls = []

    def send(self, *, key, source, destination, amount_raw):
        self.calls.append((key.reveal(), source, destination, amount_raw))
        return "A" * 64


class TestLiveCustody(_Env):
    base = {
        "SOLVENT_NANO": "live",
        "SOLVENT_NANO_ADDRESS": HOT,
        "SOLVENT_NANO_CUSTODY": "live",
        "SOLVENT_NANO_CUSTODY_KEY_REF": "env:FAKE_TEST_KEY",
        "SOLVENT_NANO_COLD_ADDRESS": COLD,
        "FAKE_TEST_KEY": "not-a-real-key",
    }

    def test_live_config_ok(self):
        self.assertEqual(NanoCustody.from_env().mode, "live")

    def test_live_requires_live_rail_and_ref(self):
        for var, val in (
            ("SOLVENT_NANO", "sim"),
            ("SOLVENT_NANO_CUSTODY_KEY_REF", "A" * 64),
            ("SOLVENT_NANO_COLD_ADDRESS", "nano_sim_cold_x"),
            ("SOLVENT_NANO_COLD_ADDRESS", HOT),
        ):
            with self.subTest(var=var), mock.patch.dict(os.environ, {var: val}):
                self.assertEqual(NanoCustody.from_env().mode, "misconfigured")

    def test_live_ignores_sim_blocks(self):
        self.book({"block_sim_a": 5 * XNO, "B" * 64: 2 * XNO})
        self.assertEqual(NanoCustody.from_env().balance_raw(), 2 * XNO)

    def test_live_sweep_refuses_without_signer(self):
        self.book({"B" * 64: 2 * XNO})
        with self.assertRaisesRegex(NanoCustodyError, "no external signer"):
            NanoCustody.from_env().execute_sweep(approve=lambda p: True)

    def test_live_sweep_with_injected_fake_signer(self):
        self.book({"B" * 64: 2 * XNO})
        signer = _FakeSigner()
        rec = NanoCustody.from_env(signer=signer).execute_sweep(approve=lambda p: True)
        self.assertEqual(signer.calls, [("not-a-real-key", HOT, COLD, 2 * XNO)])
        self.assertEqual(rec["block"], "A" * 64)
        on_disk = config_path("nano_custody_live.json").read_text()
        self.assertNotIn("not-a-real-key", on_disk)


class TestDoctor(_Env):
    def _row(self):
        from solvent.doctor import run_checks

        return next(r for r in run_checks() if r["name"] == "nano_custody")

    def test_doctor_off_ok(self):
        self.assertTrue(self._row()["ok"])

    def test_doctor_misconfigured_fails(self):
        os.environ["SOLVENT_NANO_CUSTODY"] = "sim"
        self.assertFalse(self._row()["ok"])


if __name__ == "__main__":
    unittest.main()
