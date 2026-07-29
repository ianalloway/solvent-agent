"""tests/test_init.py — unit tests for the solvent init command."""

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


class TestInit(unittest.TestCase):
    def _run_init(self, home_dir: str, force: bool = False) -> tuple[int, str]:
        """Run init.run() inside a temporary SOLVENT_HOME, return (exit_code, output)."""
        buf = io.StringIO()
        workspace_dir = str(Path(home_dir) / "workspace")
        env = {"SOLVENT_HOME": home_dir, "SOLVENT_WORKSPACE": workspace_dir}
        with mock.patch.dict(os.environ, env):
            import importlib

            import solvent.init as _init
            import solvent.paths as _paths
            import solvent.workspace as _ws

            importlib.reload(_paths)
            importlib.reload(_ws)
            importlib.reload(_init)
            with redirect_stdout(buf):
                rc = _init.run(force=force)
        return rc, buf.getvalue()

    def test_init_succeeds(self):
        with tempfile.TemporaryDirectory() as d:
            rc, out = self._run_init(d)
            self.assertEqual(rc, 0)

    def test_init_creates_data_dir(self):
        with tempfile.TemporaryDirectory() as d:
            self._run_init(d)
            self.assertTrue(Path(d, "data").is_dir())

    def test_init_creates_db(self):
        with tempfile.TemporaryDirectory() as d:
            self._run_init(d)
            self.assertTrue(Path(d, "data", "solvent.db").is_file())

    def test_init_creates_reports_dir(self):
        with tempfile.TemporaryDirectory() as d:
            self._run_init(d)
            self.assertTrue(Path(d, "data", "reports").is_dir())

    def test_init_creates_env_example(self):
        with tempfile.TemporaryDirectory() as d:
            self._run_init(d)
            env_ex = Path(d, ".env.example")
            self.assertTrue(env_ex.is_file())
            content = env_ex.read_text()
            self.assertIn("NVIDIA_API_KEY", content)
            self.assertIn("STRIPE_API_KEY", content)

    def test_init_seeds_workspace(self):
        with tempfile.TemporaryDirectory() as d:
            # workspace lives under CONFIG_DIR which is inside base_dir
            self._run_init(d)
            # soul.md or agents.md should exist somewhere under d
            md_files = list(Path(d).rglob("SOUL.md"))
            self.assertTrue(len(md_files) > 0, "SOUL.md not found after init")

    def test_init_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            rc1, _ = self._run_init(d)
            rc2, _ = self._run_init(d)
            self.assertEqual(rc1, 0)
            self.assertEqual(rc2, 0)

    def test_init_prints_home_path(self):
        with tempfile.TemporaryDirectory() as d:
            _, out = self._run_init(d)
            self.assertIn(d, out)

    def test_init_prints_next_steps(self):
        with tempfile.TemporaryDirectory() as d:
            _, out = self._run_init(d)
            self.assertIn("Next steps", out)
            self.assertIn("solvent doctor", out)

    def test_init_force_overwrites_workspace(self):
        with tempfile.TemporaryDirectory() as d:
            self._run_init(d)
            workspace_dir = str(Path(d) / "workspace")
            env = {"SOLVENT_HOME": d, "SOLVENT_WORKSPACE": workspace_dir}
            import importlib

            import solvent.paths as _paths
            import solvent.workspace as _ws

            with mock.patch.dict(os.environ, env):
                importlib.reload(_paths)
                importlib.reload(_ws)
                soul = _ws.workspace_path() / "SOUL.md"
                if soul.is_file():
                    soul.write_text("CORRUPTED", encoding="utf-8")
                    rc, _ = self._run_init(d, force=True)
                    self.assertEqual(rc, 0)
                    content = soul.read_text()
                    self.assertNotEqual(content.strip(), "CORRUPTED")



    def test_init_reports_error_when_treasury_fails(self):
        with tempfile.TemporaryDirectory() as d:
            buf = io.StringIO()
            workspace_dir = str(Path(d) / "workspace")
            env = {"SOLVENT_HOME": d, "SOLVENT_WORKSPACE": workspace_dir}
            with mock.patch.dict(os.environ, env):
                import importlib
                import solvent.init as _init
                import solvent.paths as _paths
                import solvent.workspace as _ws
                importlib.reload(_paths)
                importlib.reload(_ws)
                importlib.reload(_init)
                with mock.patch("solvent.treasury.Treasury", side_effect=RuntimeError("boom")):
                    with redirect_stdout(buf):
                        rc = _init.run()
            self.assertEqual(rc, 1)
            self.assertIn("Errors", buf.getvalue())
            self.assertIn("treasury DB: boom", buf.getvalue())



class TestInitCLI(unittest.TestCase):
    def test_main_exits_zero(self):
        with tempfile.TemporaryDirectory() as d:
            workspace_dir = str(Path(d) / "workspace")
            env = {"SOLVENT_HOME": d, "SOLVENT_WORKSPACE": workspace_dir}
            with mock.patch.dict(os.environ, env):
                import importlib

                import solvent.init as _init
                import solvent.paths as _paths
                import solvent.workspace as _ws

                importlib.reload(_paths)
                importlib.reload(_ws)
                importlib.reload(_init)
                # __main__.py pops "init" before calling main(), so argv has no subcommand
                with mock.patch.object(sys, "argv", ["solvent"]):
                    with self.assertRaises(SystemExit) as ctx:
                        with mock.patch("sys.stdout", new_callable=io.StringIO):
                            _init.main()
            self.assertEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
