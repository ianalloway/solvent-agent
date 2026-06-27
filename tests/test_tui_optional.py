"""tui must import without `rich` installed and degrade cleanly.

These tests intentionally do NOT importorskip rich — importing the module and
calling run() must work (or fail gracefully) regardless of whether the
optional extra is present.
"""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from solvent import tui


class TestTuiOptional(unittest.TestCase):
    def test_import_does_not_crash(self):
        self.assertTrue(callable(tui.run))

    def test_run_degrades_when_rich_missing(self):
        buf = io.StringIO()
        with patch.object(tui, "_RICH_AVAILABLE", False), redirect_stdout(buf):
            tui.run()  # must return cleanly, not raise/exit
        self.assertIn("rich", buf.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
