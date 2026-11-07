from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests" / "alerts"))

from alert_fakes import TEST_SETTINGS, FakeAlertStore  # noqa: E402
from api import index  # noqa: E402
from retriever_lost_found.alerts import matching  # noqa: E402

AUTH = {"Authorization": "Bearer cron-test-secret"}
FROM_ENVIRONMENT = "retriever_lost_found.web.cron.SupabaseAlertStore.from_environment"


def sync_result(status: str = "succeeded", inserted: int = 3) -> dict:
    return {
        "date": "2026-11-06",
        "status": status,
        "source": {"source": "partner_api", "status": status, "inserted": inserted},
    }


def judge(query, candidates):
    return []


class MatchingHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(index.app)

    def call_partner(self, result: dict):
        with (
            patch.dict(os.environ, {"CRON_SECRET": "cron-test-secret"}),
            patch("retriever_lost_found.web.cron.run_incremental_sync", return_value=result),
        ):
            return self.client.get("/api/cron/partner-sync", headers=AUTH)

    def test_new_items_start_matching_for_that_source(self) -> None:
        store = FakeAlertStore()
        with (
            patch.object(matching, "SIMILARITY_JUDGE", judge),
            patch(FROM_ENVIRONMENT, return_value=store),
            patch("retriever_lost_found.web.cron.run_match_pass") as run_match,
        ):
            response = self.call_partner(sync_result())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), sync_result())  # 수집 응답은 그대로다(설계 6.1)
        run_match.assert_called_once()
        self.assertIs(run_match.call_args.args[0], store)
        self.assertIs(run_match.call_args.args[1], judge)
        self.assertEqual(run_match.call_args.kwargs["item_source_code"], "partner_api")

    def test_no_new_items_or_a_failed_sync_skips_matching(self) -> None:
        for result in (sync_result(inserted=0), sync_result(status="failed")):
            with (
                self.subTest(result=result),
                patch.object(matching, "SIMILARITY_JUDGE", judge),
                patch(FROM_ENVIRONMENT) as from_environment,
            ):
                self.call_partner(result)
            from_environment.assert_not_called()

    def test_without_a_judge_matching_does_not_run(self) -> None:
        with patch.object(matching, "SIMILARITY_JUDGE", None), patch(FROM_ENVIRONMENT) as from_environment:
            self.call_partner(sync_result())

        from_environment.assert_not_called()

    def test_matching_failure_does_not_change_the_sync_response(self) -> None:
        with (
            patch.object(matching, "SIMILARITY_JUDGE", judge),
            patch(FROM_ENVIRONMENT, return_value=FakeAlertStore()),
            patch("retriever_lost_found.web.cron.run_match_pass", side_effect=RuntimeError("boom")),
        ):
            response = self.call_partner(sync_result())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), sync_result())


class AlertSendEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(index.app)

    def call(self, headers=None, *, settings=TEST_SETTINGS, result=None):
        with (
            patch.dict(os.environ, {"CRON_SECRET": "cron-test-secret"}),
            patch("retriever_lost_found.web.cron.load_alert_settings", return_value=settings),
            patch(FROM_ENVIRONMENT, return_value=FakeAlertStore()) as from_environment,
            patch(
                "retriever_lost_found.web.cron.run_send_pass",
                return_value=result or {"status": "succeeded", "messages_sent": 2},
            ) as run_send,
        ):
            response = self.client.get("/api/cron/alert-send", headers=headers)
        return response, run_send, from_environment

    def test_requires_the_cron_secret(self) -> None:
        # 절대 규칙 8
        response, run_send, _ = self.call()

        self.assertEqual(response.status_code, 401)
        run_send.assert_not_called()

    def test_runs_the_send_pass_with_the_time_budget(self) -> None:
        response, run_send, from_environment = self.call(AUTH)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "succeeded", "messages_sent": 2})
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIs(run_send.call_args.args[0], from_environment.return_value)
        self.assertIs(run_send.call_args.args[2], TEST_SETTINGS)
        self.assertIn("deadline", run_send.call_args.kwargs)

    def test_without_mail_settings_is_503(self) -> None:
        response, run_send, _ = self.call(AUTH, settings=None)

        self.assertEqual(response.status_code, 503)
        run_send.assert_not_called()

    def test_without_a_supabase_key_is_503(self) -> None:
        # AGENTS.md 4장 — 설정이 없으면 503. 입력 오류(400)로 보이지 않게 한다.
        with (
            patch.dict(os.environ, {"CRON_SECRET": "cron-test-secret"}),
            patch("retriever_lost_found.web.cron.load_alert_settings", return_value=TEST_SETTINGS),
            patch(FROM_ENVIRONMENT, side_effect=ValueError("SUPABASE_SECRET_KEY가 설정되지 않았습니다.")),
            patch("retriever_lost_found.web.cron.run_send_pass") as run_send,
        ):
            response = self.client.get("/api/cron/alert-send", headers=AUTH)

        self.assertEqual(response.status_code, 503)
        self.assertIn("SUPABASE_SECRET_KEY", response.json()["error"])
        run_send.assert_not_called()

    def test_failed_send_pass_is_500(self) -> None:
        response, _, _ = self.call(AUTH, result={"status": "failed", "messages_sent": 0})

        self.assertEqual(response.status_code, 500)


if __name__ == "__main__":
    unittest.main()
