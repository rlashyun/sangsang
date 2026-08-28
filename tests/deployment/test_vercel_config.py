from __future__ import annotations

import json
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class VercelConfigTests(unittest.TestCase):
    def test_daily_sync_runs_at_nine_am_korea_time(self) -> None:
        config = json.loads((PROJECT_ROOT / "vercel.json").read_text(encoding="utf-8"))

        self.assertEqual(
            config["crons"],
            [
                {"path": "/api/cron/partner-sync", "schedule": "0 0 * * *"},
                {"path": "/api/cron/police-sync", "schedule": "0 0 * * *"},
            ],
        )
        self.assertGreaterEqual(config["functions"]["api/index.py"]["maxDuration"], 300)


if __name__ == "__main__":
    unittest.main()
