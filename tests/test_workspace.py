import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from solvent import workspace


class TestWorkspace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name) / "workspace"
        self._env = patch.dict(os.environ, {"SOLVENT_WORKSPACE": str(self.ws)})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self.tmp.cleanup()

    def test_seed_workspace(self):
        path = workspace.seed_workspace()
        self.assertTrue((path / "SOUL.md").is_file())
        self.assertTrue((path / "BRAIN.md").is_file())
        self.assertTrue((path / "AGENTS.md").is_file())
        soul = (path / "SOUL.md").read_text(encoding="utf-8")
        self.assertIn("SOLVENT", soul)

    def test_build_system_prompt_includes_soul_and_brain(self):
        workspace.seed_workspace()
        prompt = workspace.build_system_prompt(session_kind="main")
        self.assertIn("SOLVENT", prompt)
        self.assertIn("BRAIN", prompt)
        self.assertIn("AGENTS.md", prompt)
        self.assertIn("economic kernel", prompt.lower())

    def test_shared_session_skips_memory_file(self):
        workspace.seed_workspace()
        (workspace.workspace_path() / "MEMORY.md").write_text(
            "SECRET_USER_FACT\n", encoding="utf-8"
        )
        main_prompt = workspace.build_system_prompt(session_kind="main")
        shared_prompt = workspace.build_system_prompt(session_kind="shared")
        self.assertIn("SECRET_USER_FACT", main_prompt)
        self.assertNotIn("SECRET_USER_FACT", shared_prompt)

    def test_append_daily_memory(self):
        workspace.seed_workspace()
        path = workspace.append_daily_memory("test event")
        self.assertTrue(path.is_file())
        self.assertIn("test event", path.read_text(encoding="utf-8"))

    def test_promote_skill(self):
        skills_dir = Path(self.tmp.name) / "skills"
        with patch.object(workspace, "SKILLS_DIR", skills_dir):
            path = workspace.promote_skill("margin-hint", "# Margin\nAlways quote first.")
            self.assertTrue(path.name == "SKILL.md")
            self.assertTrue(path.is_file())
            prompt = workspace.build_system_prompt()
            self.assertIn("Always quote first", prompt)
