"""Unit tests for the Nano settle rail (issue #83). No live network."""

from __future__ import annotations

import os
import tempfile
import unittest
from decimal import ROUND_HALF_UP, Decimal, localcontext
from unittest import mock

from solvent.nano_client import (
    DEFAULT_SIM_RATE_USD,
    NanoClient,
    NanoClientError,
    cents_to_raw,
    is_block_hash,
    is_nano_address,
)

# Valid-looking nano_ address for live-mode tests (checksum not verified in v1).
LIVE_ADDR = "nano_1qato4k7z3spc8gq1zyd8xeqfbzsoxwo36a45ozbrxcatut7up8ohyardu1z"
LIVE_HASH = "87434F8041869A01C8F6F263B87972D7BA443A72E0A97D7A3FD0CCC2358FD6F9"


class TestCentsToRaw(unittest.TestCase):
    def test_deterministic_integer_math(self):
        raw = cents_to_raw(4900, Decimal("0.85"))
        with localcontext() as ctx:
            ctx.prec = 80
            expected = (
                Decimal("49") / Decimal("0.85") * (Decimal(10) ** 30)
            ).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        self.assertEqual(raw, str(int(expected)))
        self.assertTrue(raw.isdigit())

    def test_zero(self):
        self.assertEqual(cents_to_raw(0, "1.0"), "0")

    def test_rejects_bad_rate(self):
        with self.assertRaises(ValueError):
            cents_to_raw(100, "0")
        with self.assertRaises(ValueError):
            cents_to_raw(100, "-1")
        with self.assertRaises(ValueError):
            cents_to_raw(100, "nan")

    def test_rejects_negative_cents(self):
        with self.assertRaises(ValueError):
            cents_to_raw(-1, "1.0")


class TestAddressHelpers(unittest.TestCase):
    def test_address_and_hash_shapes(self):
        self.assertTrue(is_nano_address(LIVE_ADDR))
        self.assertFalse(is_nano_address("nano_sim_abc"))
        self.assertFalse(is_nano_address("nano_short"))
        self.assertTrue(is_block_hash(LIVE_HASH))
        self.assertFalse(is_block_hash("block_sim_x"))


class TestNanoClientSim(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._env = mock.patch.dict(
            os.environ,
            {
                "SOLVENT_NANO": "sim",
                "SOLVENT_HOME": self._tmpdir.name,
                "SOLVENT_NANO_RPC": "",
                "SOLVENT_NANO_ADDRESS": "",
                "SOLVENT_FORCE_NANO_SIMULATE": "",
            },
            clear=False,
        )
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmpdir.cleanup()

    def test_enabled_sim_creates_nano_sim_destination(self):
        client = NanoClient()
        self.assertTrue(client.enabled)
        self.assertTrue(client.simulate)
        inv = client.create_nano_invoice("JOB1", 4900, customer_email="a@b.com")
        self.assertTrue(inv["destination"].startswith("nano_sim_"))
        self.assertTrue(inv["id"].startswith("inv_sim_"))
        self.assertEqual(inv["amount_cents"], 4900)
        self.assertEqual(inv["amount_raw"], cents_to_raw(4900, DEFAULT_SIM_RATE_USD))
        self.assertEqual(inv["rail"], "nano")
        self.assertTrue(inv["simulated"])
        self.assertTrue(inv["url"].startswith("nano:"))

    def test_destination_deterministic_per_job(self):
        client = NanoClient()
        a = client.create_nano_invoice("SAME", 1000)
        b = client.create_nano_invoice("SAME", 2000)
        self.assertEqual(a["destination"], b["destination"])
        other = client.create_nano_invoice("OTHER", 1000)
        self.assertNotEqual(a["destination"], other["destination"])

    def test_verify_accepts_block_sim(self):
        client = NanoClient()
        inv = client.create_nano_invoice("JOBV", 2500)
        block = client.mint_sim_block(inv)
        payment = client.verify_nano_block(inv, block)
        self.assertTrue(payment["paid"])
        self.assertEqual(payment["payment_ref"], block)
        self.assertEqual(payment["stripe_ref"], block)
        self.assertEqual(payment["rail"], "nano")
        self.assertTrue(payment["simulated"])

    def test_reject_underpay(self):
        client = NanoClient()
        inv = client.create_nano_invoice("JOBU", 5000)
        need = int(inv["amount_raw"])
        payment = client.verify_nano_block(
            inv,
            "block_sim_under",
            block={"amount_raw": str(need - 1), "destination": inv["destination"]},
        )
        self.assertFalse(payment["paid"])
        self.assertIn("underpay", payment["reason"])

    def test_reject_wrong_destination(self):
        client = NanoClient()
        inv = client.create_nano_invoice("JOBD", 5000)
        payment = client.verify_nano_block(
            inv,
            "block_sim_wrongdest",
            block={"destination": "nano_sim_notmine", "amount_raw": inv["amount_raw"]},
        )
        self.assertFalse(payment["paid"])
        self.assertEqual(payment["reason"], "wrong destination")

    def test_reject_replay(self):
        client = NanoClient()
        inv = client.create_nano_invoice("JOBR", 1000)
        block = "block_sim_replay1"
        first = client.verify_nano_block(inv, block)
        self.assertTrue(first["paid"])
        inv2 = client.create_nano_invoice("JOBR2", 1000)
        second = client.verify_nano_block(inv2, block)
        self.assertFalse(second["paid"])
        self.assertIn("replay", second["reason"])

    def test_reject_expired(self):
        client = NanoClient()
        inv = client.create_nano_invoice("JOBE", 1000, ttl_seconds=10, now=1_000.0)
        payment = client.verify_nano_block(
            inv, "block_sim_expired", now=1_000.0 + 11
        )
        self.assertFalse(payment["paid"])
        self.assertEqual(payment["reason"], "invoice expired")

    def test_reject_non_send_subtype(self):
        client = NanoClient()
        inv = client.create_nano_invoice("JOBS", 1000)
        payment = client.verify_nano_block(
            inv,
            "block_sim_receive",
            block={"subtype": "receive", "destination": inv["destination"]},
        )
        self.assertFalse(payment["paid"])
        self.assertIn("subtype", payment["reason"])

    def test_reject_non_sim_block_token(self):
        client = NanoClient()
        inv = client.create_nano_invoice("JOBX", 1000)
        payment = client.verify_nano_block(inv, "deadbeef" * 4)
        self.assertFalse(payment["paid"])


class TestNanoClientOptIn(unittest.TestCase):
    def test_unset_is_disabled(self):
        with mock.patch.dict(
            os.environ,
            {
                "SOLVENT_NANO": "",
                "SOLVENT_NANO_RPC": "",
                "SOLVENT_NANO_ADDRESS": "",
                "SOLVENT_FORCE_NANO_SIMULATE": "",
            },
            clear=False,
        ):
            client = NanoClient()
            self.assertFalse(client.enabled)
            self.assertEqual(client.mode, "unset")
            with self.assertRaises(NanoClientError):
                client.create_nano_invoice("J", 100)

    def test_partial_live_is_misconfigured(self):
        with mock.patch.dict(
            os.environ,
            {
                "SOLVENT_NANO": "",
                "SOLVENT_NANO_RPC": "https://example.invalid/rpc",
                "SOLVENT_NANO_ADDRESS": "",
                "SOLVENT_FORCE_NANO_SIMULATE": "",
            },
            clear=False,
        ):
            client = NanoClient()
            self.assertEqual(client.mode, "misconfigured")
            self.assertFalse(client.enabled)

    def test_sim_plus_rpc_is_misconfigured(self):
        with mock.patch.dict(
            os.environ,
            {
                "SOLVENT_NANO": "sim",
                "SOLVENT_NANO_RPC": "https://example.invalid/rpc",
                "SOLVENT_NANO_ADDRESS": "",
                "SOLVENT_FORCE_NANO_SIMULATE": "",
            },
            clear=False,
        ):
            client = NanoClient()
            self.assertEqual(client.mode, "misconfigured")
            self.assertFalse(client.enabled)

    def test_live_requires_full_config(self):
        with mock.patch.dict(
            os.environ,
            {
                "SOLVENT_NANO": "live",
                "SOLVENT_NANO_RPC": "https://example.invalid/rpc",
                "SOLVENT_NANO_ADDRESS": LIVE_ADDR,
                "SOLVENT_NANO_RATE_USD": "0.90",
                "SOLVENT_FORCE_NANO_SIMULATE": "",
            },
            clear=False,
        ):
            client = NanoClient(rpc_post=lambda url, payload: {"node_vendor": "test"})
            self.assertEqual(client.mode, "live")
            self.assertTrue(client.enabled)
            self.assertFalse(client.simulate)
            inv = client.create_nano_invoice("JLIVE", 1000)
            self.assertEqual(inv["destination"], LIVE_ADDR)
            self.assertTrue(inv["id"].startswith("inv_live_"))
            self.assertFalse(inv["simulated"])


class TestNanoClientLiveVerify(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._env = mock.patch.dict(
            os.environ,
            {
                "SOLVENT_NANO": "live",
                "SOLVENT_HOME": self._tmpdir.name,
                "SOLVENT_NANO_RPC": "https://example.invalid/rpc",
                "SOLVENT_NANO_ADDRESS": LIVE_ADDR,
                "SOLVENT_NANO_RATE_USD": "0.85",
                "SOLVENT_FORCE_NANO_SIMULATE": "",
            },
            clear=False,
        )
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmpdir.cleanup()

    def _rpc(self, url, payload):
        action = payload.get("action")
        if action == "version":
            return {"node_vendor": "Nano Test", "rpc_version": "1"}
        if action == "block_info":
            return {
                "amount": self._amount,
                "confirmed": "true",
                "subtype": "send",
                "contents": {
                    "link_as_account": self._dest,
                    "type": "state",
                },
            }
        raise AssertionError(f"unexpected action {action}")

    def test_live_verify_accepts_confirmed_send(self):
        client = NanoClient(rpc_post=self._rpc)
        inv = client.create_nano_invoice("JL1", 1000)
        self._amount = inv["amount_raw"]
        self._dest = LIVE_ADDR
        payment = client.verify_nano_block(inv, LIVE_HASH)
        self.assertTrue(payment["paid"])
        self.assertFalse(payment["simulated"])
        self.assertEqual(payment["payment_ref"], LIVE_HASH)

    def test_live_rejects_sim_proof(self):
        client = NanoClient(rpc_post=self._rpc)
        inv = client.create_nano_invoice("JL2", 1000)
        payment = client.verify_nano_block(inv, "block_sim_nope")
        self.assertFalse(payment["paid"])
        self.assertIn("simulate", payment["reason"])

    def test_live_rejects_underpay(self):
        client = NanoClient(rpc_post=self._rpc)
        inv = client.create_nano_invoice("JL3", 5000)
        self._amount = str(int(inv["amount_raw"]) - 1)
        self._dest = LIVE_ADDR
        payment = client.verify_nano_block(inv, LIVE_HASH)
        self.assertFalse(payment["paid"])
        self.assertIn("underpay", payment["reason"])

    def test_live_rejects_wrong_destination(self):
        client = NanoClient(rpc_post=self._rpc)
        inv = client.create_nano_invoice("JL4", 1000)
        self._amount = inv["amount_raw"]
        self._dest = "nano_1ipx847tk8o46pwxt5qjdbncjqcbwcc1rrmqnkztrfjy5k7z4imsrata9est"
        payment = client.verify_nano_block(inv, LIVE_HASH)
        self.assertFalse(payment["paid"])
        self.assertEqual(payment["reason"], "wrong destination")

    def test_mint_sim_block_refused_in_live(self):
        client = NanoClient(rpc_post=self._rpc)
        with self.assertRaises(NanoClientError):
            client.mint_sim_block()


class TestNanoStagesIntegration(unittest.TestCase):
    def test_nano_sim_job_earns_and_fulfills(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                os.environ,
                {
                    "SOLVENT_NANO": "sim",
                    "SOLVENT_HOME": tmp,
                    "SOLVENT_NANO_RPC": "",
                    "SOLVENT_NANO_ADDRESS": "",
                    "STRIPE_API_KEY": "",
                    "SOLVENT_FORCE_STRIPE_SIMULATE": "1",
                    "SOLVENT_DELIVERY_SECRET": "x" * 32,
                    "NVIDIA_API_KEY": "",
                },
                clear=False,
            ):
                from solvent.agent import Solvent

                agent = Solvent(seed_cents=10_000, fresh=True, sync_payment=True)
                job = {
                    "id": "J-nano-1",
                    "topic": "Nano settle smoke",
                    "budget_cents": 8_000,
                    "customer_email": "buyer@example.com",
                    "est_tokens": 2_000,
                    "market_data_calls": 1,
                    "web_search_calls": 2,
                }
                result = agent.handle_job(job)
                self.assertNotEqual(result.get("stage"), "declined")
                stages = [e.get("stage") for e in agent.log]
                self.assertIn("invoice", stages)
                self.assertIn("paid", stages)
                inv = agent.t.get_nano_invoice("J-nano-1")
                self.assertIsNotNone(inv)
                self.assertEqual(inv["status"], "paid")
                self.assertTrue(str(inv["destination"]).startswith("nano_sim_"))
                refs = [
                    e.resolved_payment_ref()
                    for e in agent.t.entries
                    if e.job_id == "J-nano-1" and e.kind == "revenue"
                ]
                self.assertTrue(refs)
                self.assertTrue(str(refs[0]).startswith("block_sim_"))

    def test_default_path_still_stripe_without_nano(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                os.environ,
                {
                    "SOLVENT_NANO": "",
                    "SOLVENT_NANO_RPC": "",
                    "SOLVENT_NANO_ADDRESS": "",
                    "SOLVENT_FORCE_NANO_SIMULATE": "",
                    "SOLVENT_HOME": tmp,
                    "STRIPE_API_KEY": "",
                    "SOLVENT_FORCE_STRIPE_SIMULATE": "1",
                    "SOLVENT_DELIVERY_SECRET": "x" * 32,
                    "NVIDIA_API_KEY": "",
                },
                clear=False,
            ):
                from solvent.agent import Solvent

                agent = Solvent(seed_cents=10_000, fresh=True, sync_payment=True)
                self.assertFalse(agent.nano.enabled)
                job = {
                    "id": "J-stripe-1",
                    "topic": "Stripe default smoke",
                    "budget_cents": 8_000,
                    "customer_email": "buyer@example.com",
                    "est_tokens": 2_000,
                    "market_data_calls": 1,
                    "web_search_calls": 2,
                }
                agent.handle_job(job)
                self.assertIsNone(agent.t.get_nano_invoice("J-stripe-1"))
                checkout = agent.t.get_checkout("J-stripe-1")
                self.assertIsNotNone(checkout)
                self.assertEqual(checkout.get("rail") or "stripe", "stripe")


class TestDoctorNano(unittest.TestCase):
    def test_doctor_reports_sim(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                os.environ,
                {
                    "SOLVENT_NANO": "sim",
                    "SOLVENT_HOME": tmp,
                    "SOLVENT_NANO_RPC": "",
                    "SOLVENT_NANO_ADDRESS": "",
                    "SOLVENT_NANO_RATE_USD": "0.85",
                    "SOLVENT_FORCE_NANO_SIMULATE": "",
                },
                clear=False,
            ):
                from solvent.doctor import run_checks

                check = next(c for c in run_checks() if c["name"] == "nano_rail")
                self.assertTrue(check["ok"])
                self.assertIn("SOLVENT_NANO=sim", check["detail"])
                self.assertIn("0.85", check["detail"])
                self.assertIn("verify-only", check["detail"])

    def test_doctor_reports_unset(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                os.environ,
                {
                    "SOLVENT_NANO": "",
                    "SOLVENT_NANO_RPC": "",
                    "SOLVENT_NANO_ADDRESS": "",
                    "SOLVENT_FORCE_NANO_SIMULATE": "",
                    "SOLVENT_HOME": tmp,
                },
                clear=False,
            ):
                from solvent.doctor import run_checks

                check = next(c for c in run_checks() if c["name"] == "nano_rail")
                self.assertTrue(check["ok"])
                self.assertIn("unset", check["detail"])

    def test_doctor_reports_misconfigured_partial_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                os.environ,
                {
                    "SOLVENT_NANO": "",
                    "SOLVENT_NANO_RPC": "https://example.invalid/rpc",
                    "SOLVENT_NANO_ADDRESS": "",
                    "SOLVENT_FORCE_NANO_SIMULATE": "",
                    "SOLVENT_HOME": tmp,
                },
                clear=False,
            ):
                from solvent.doctor import run_checks

                check = next(c for c in run_checks() if c["name"] == "nano_rail")
                self.assertFalse(check["ok"])
                self.assertIn("incomplete", check["detail"].lower())

    def test_doctor_reports_live_when_rpc_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                os.environ,
                {
                    "SOLVENT_NANO": "live",
                    "SOLVENT_HOME": tmp,
                    "SOLVENT_NANO_RPC": "https://example.invalid/rpc",
                    "SOLVENT_NANO_ADDRESS": LIVE_ADDR,
                    "SOLVENT_NANO_RATE_USD": "0.85",
                    "SOLVENT_FORCE_NANO_SIMULATE": "",
                },
                clear=False,
            ):
                with mock.patch(
                    "solvent.nano_client.NanoClient.probe_rpc",
                    return_value={"ok": True, "detail": "reachable (test)"},
                ):
                    from solvent.doctor import run_checks

                    check = next(c for c in run_checks() if c["name"] == "nano_rail")
                    self.assertTrue(check["ok"])
                    self.assertIn("SOLVENT_NANO=live", check["detail"])


if __name__ == "__main__":
    unittest.main()
