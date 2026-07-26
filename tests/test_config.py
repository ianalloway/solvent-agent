"""Tests for SOLVENT user config (onboarding preferences)."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from solvent.config import (
    CONFIG_PATH,
    SolventConfig,
    apply_config,
    config_exists,
    default_config,
    load_config,
    save_config,
)


class TestConfig(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_path = CONFIG_PATH
        import solvent.config as cfg_mod

        cfg_mod.CONFIG_DIR = Path(self._tmpdir.name) / ".solvent"
        cfg_mod.CONFIG_PATH = cfg_mod.CONFIG_DIR / "config.json"

    def tearDown(self):
        import solvent.config as cfg_mod

        cfg_mod.CONFIG_DIR = self._orig_path.parent
        cfg_mod.CONFIG_PATH = self._orig_path
        self._tmpdir.cleanup()

    def test_default_config(self):
        cfg = default_config()
        self.assertTrue(cfg.onboarded)
        self.assertEqual(cfg.model, "offline")
        self.assertEqual(cfg.interaction_mode, "batch")

    def test_save_and_load_roundtrip(self):
        cfg = SolventConfig(
            onboarded=True,
            model="nemotron",
            interaction_mode="interactive",
            stripe_test_mode=True,
        )
        path = save_config(cfg)
        self.assertTrue(path.is_file())
        loaded = load_config()
        assert loaded is not None
        self.assertEqual(loaded.model, "nemotron")
        self.assertEqual(loaded.interaction_mode, "interactive")
        self.assertTrue(loaded.stripe_test_mode)

    def test_config_exists(self):
        self.assertFalse(config_exists())
        save_config(default_config())
        self.assertTrue(config_exists())

    def test_invalid_model_raises(self):
        cfg = SolventConfig(model="invalid")
        with self.assertRaises(ValueError):
            cfg.validate()

    def test_apply_config_offline_nemotron(self):
        from solvent import nemotron

        apply_config(SolventConfig(model="offline"))
        self.assertTrue(nemotron._force_offline)

        apply_config(SolventConfig(model="nemotron", nemotron_model="test/model"))
        self.assertFalse(nemotron._force_offline)
        self.assertEqual(nemotron.MODEL, "test/model")

    def test_apply_config_stripe_simulate(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SOLVENT_FORCE_STRIPE_SIMULATE", None)
            apply_config(SolventConfig(stripe_test_mode=False))
            self.assertEqual(os.environ.get("SOLVENT_FORCE_STRIPE_SIMULATE"), "1")

            apply_config(SolventConfig(stripe_test_mode=True))
            self.assertNotIn("SOLVENT_FORCE_STRIPE_SIMULATE", os.environ)

    def test_from_dict_ignores_unknown_keys(self):
        cfg = SolventConfig.from_dict({"model": "offline", "extra_field": 99})
        self.assertEqual(cfg.model, "offline")

    def test_saved_json_shape(self):
        save_config(default_config())
        import solvent.config as cfg_mod

        data = json.loads(cfg_mod.CONFIG_PATH.read_text())
        self.assertIn("onboarded", data)
        self.assertIn("interaction_mode", data)


if __name__ == "__main__":
    unittest.main()
