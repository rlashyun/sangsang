from __future__ import annotations

import unittest
from pathlib import Path

from retriever_lost_found.alerts.settings import REQUIRED_NAMES

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class AlertEnvironmentTests(unittest.TestCase):
    def test_env_example_and_ci_list_every_alert_setting(self) -> None:
        # AGENTS.md 6장 — 환경 변수를 추가하면 .env.example·README·CI env를 같이 갱신한다.
        env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
        ci = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

        for name in REQUIRED_NAMES:
            with self.subTest(name=name):
                self.assertIn(f"{name}=", env_example)
                self.assertIn(f"{name}:", ci)
                self.assertIn(f"`{name}`", readme)
        self.assertIn("ALERT_DAILY_SEND_LIMIT=", env_example)


if __name__ == "__main__":
    unittest.main()
