import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from solvent import gateway
from solvent.gateway import Gateway
from solvent.agent import Solvent
from solvent.rate_limit import RateLimiter
from solvent.treasury import Treasury


class TestDashboardGateway(unittest.TestCase):
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

    def test_dashboard_status_command(self):
        reply = self.gw.handle_inbound("dashboard", "web-1", "/status")
        self.assertIn("Balance", reply)


if __name__ == "__main__":
    unittest.main()
