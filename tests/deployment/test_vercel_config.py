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

    def test_functions_run_in_seoul_next_to_their_data(self) -> None:
        # PRD 5(성능) — Supabase(ap-northeast-2)·경찰청 API·카카오 API가 모두 한국에 있다.
        # Vercel 기본값 iad1(us-east-1)에 두면 DB 왕복마다 태평양을 건넌다.
        config = json.loads((PROJECT_ROOT / "vercel.json").read_text(encoding="utf-8"))

        self.assertEqual(config["regions"], ["icn1"])

    def test_single_region_keeps_the_deployment_within_the_hobby_limit(self) -> None:
        # Hobby는 단일 리전만 허용한다. 두 곳 이상이면 빌드 전에 배포가 실패한다.
        config = json.loads((PROJECT_ROOT / "vercel.json").read_text(encoding="utf-8"))

        self.assertEqual(len(config["regions"]), 1)


if __name__ == "__main__":
    unittest.main()
