from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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


class SyncCronTests(unittest.TestCase):
    def test_sync_crons_leave_alerts_to_the_send_cron(self) -> None:
        # 설계 6.1(2026-11-15) — 매칭은 발송 크론 앞단에서 한다. 수집 크론은 알림 저장소를 열지 않는다.
        client = TestClient(index.app)
        for path in ("/api/cron/partner-sync", "/api/cron/police-sync-afternoon"):
            with (
                self.subTest(path=path),
                patch.dict(os.environ, {"CRON_SECRET": "cron-test-secret"}),
                patch("retriever_lost_found.web.cron.run_incremental_sync", return_value=sync_result()),
                patch("retriever_lost_found.web.cron.dispatch_embeddings"),
                patch(FROM_ENVIRONMENT) as from_environment,
                patch("retriever_lost_found.web.cron.run_match_pass") as run_match,
            ):
                response = client.get(path, headers=AUTH)

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), sync_result())
            from_environment.assert_not_called()
            run_match.assert_not_called()


class AlertSendEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(index.app)

    def call(self, headers=None, *, settings=TEST_SETTINGS, result=None, match_side_effect=None):
        steps = MagicMock()
        steps.match.side_effect = match_side_effect or (
            lambda store, judge, *, item_source_code, deadline: {"status": "succeeded", "items_queued": 1}
        )
        steps.send.return_value = result or {"status": "succeeded", "messages_sent": 2}
        with (
            patch.dict(os.environ, {"CRON_SECRET": "cron-test-secret"}),
            patch("retriever_lost_found.web.cron.load_alert_settings", return_value=settings),
            patch(FROM_ENVIRONMENT, return_value=FakeAlertStore()) as from_environment,
            patch("retriever_lost_found.web.cron.run_match_pass", steps.match),
            patch("retriever_lost_found.web.cron.run_send_pass", steps.send),
        ):
            response = self.client.get("/api/cron/alert-send", headers=headers)
        self.steps = steps
        return response, steps.send, from_environment

    def test_requires_the_cron_secret(self) -> None:
        # 절대 규칙 8
        response, run_send, _ = self.call()

        self.assertEqual(response.status_code, 401)
        run_send.assert_not_called()

    def test_matches_both_sources_then_sends_within_the_time_budget(self) -> None:
        response, run_send, from_environment = self.call(AUTH)

        self.assertEqual(response.status_code, 200)
        matched = {"status": "succeeded", "items_queued": 1}
        self.assertEqual(
            response.json(),
            {"status": "succeeded", "messages_sent": 2,
             "matching": {"partner_api": matched, "police_api": matched}},
        )
        self.assertEqual(response.headers["cache-control"], "no-store")
        # 매칭(출처별) → 발송 순서. 판정은 운영 검색 방법(alerts/judge.py)이다.
        self.assertEqual([name for name, _, _ in self.steps.mock_calls], ["match", "match", "send"])
        match_calls = self.steps.match.call_args_list
        self.assertEqual([c.kwargs["item_source_code"] for c in match_calls], ["partner_api", "police_api"])
        self.assertTrue(all(c.args[0] is from_environment.return_value for c in match_calls))
        # 2026-11-23 사용자 결정 — OpenRouter 키가 있으면 같은 판정에 Jev 제외를 더한다(matching.cron_judge).
        judge = match_calls[0].args[1]
        self.assertTrue(all(c.args[1] is judge for c in match_calls))
        self.assertIs(getattr(judge, "func", judge), matching.SIMILARITY_JUDGE)
        self.assertIs(run_send.call_args.args[0], from_environment.return_value)
        self.assertIs(run_send.call_args.args[2], TEST_SETTINGS)
        self.assertLess(match_calls[0].kwargs["deadline"], run_send.call_args.kwargs["deadline"])

    def test_a_matching_failure_still_sends_and_reports_500(self) -> None:
        def match(store, judge, *, item_source_code, deadline):
            if item_source_code == "partner_api":
                raise RuntimeError("Supabase 알림 API 연결 실패")
            return {"status": "succeeded", "items_queued": 0}

        response, run_send, _ = self.call(AUTH, match_side_effect=match)

        run_send.assert_called_once()  # 출처 독립 — 한쪽 매칭이 실패해도 다른 쪽과 발송은 계속한다
        self.assertEqual([c.kwargs["item_source_code"] for c in self.steps.match.call_args_list],
                         ["partner_api", "police_api"])
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["matching"]["partner_api"],
                         {"status": "failed", "error_message": "RuntimeError"})

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
