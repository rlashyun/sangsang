from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from api import index


class CronEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(index.app)
        # Unit tests must never dispatch a real authenticated worker request.
        dispatcher = patch("retriever_lost_found.web.cron.dispatch_embeddings")
        self.dispatch = dispatcher.start()
        self.addCleanup(dispatcher.stop)

    def test_rejects_missing_or_invalid_authorization(self) -> None:
        with patch.dict(os.environ, {"CRON_SECRET": "cron-test-secret"}):
            response = self.client.get("/api/cron/partner-sync")

        self.assertEqual(response.status_code, 401)

    def test_partner_endpoint_runs_partner_incremental_sync(self) -> None:
        result = {
            "date": "2026-08-27",
            "status": "succeeded",
            "source": {"source": "partner_api", "status": "succeeded"},
        }
        with (
            patch.dict(os.environ, {"CRON_SECRET": "cron-test-secret"}),
            patch(
                "retriever_lost_found.web.cron.run_incremental_sync",
                return_value=result,
            ) as sync,
        ):
            response = self.client.get(
                "/api/cron/partner-sync",
                headers={"Authorization": "Bearer cron-test-secret"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), result)
        self.assertEqual(response.headers["cache-control"], "no-store")
        sync.assert_called_once_with("partner", cleanup_expired=True)

    def test_police_endpoint_runs_police_incremental_sync(self) -> None:
        result = {
            "date": "2026-08-27",
            "status": "succeeded",
            "source": {"source": "police_api", "status": "succeeded"},
        }
        with (
            patch.dict(os.environ, {"CRON_SECRET": "cron-test-secret"}),
            patch(
                "retriever_lost_found.web.cron.run_incremental_sync",
                return_value=result,
            ) as sync,
        ):
            response = self.client.get(
                "/api/cron/police-sync",
                headers={"Authorization": "Bearer cron-test-secret"},
            )

        self.assertEqual(response.status_code, 200)
        sync.assert_called_once_with("police", cleanup_expired=True)

    def test_reports_failed_sync_as_server_error(self) -> None:
        result = {
            "date": "2026-08-27",
            "status": "failed",
            "source": {"source": "partner_api", "status": "failed"},
        }
        with (
            patch.dict(os.environ, {"CRON_SECRET": "cron-test-secret"}),
            patch(
                "retriever_lost_found.web.cron.run_incremental_sync",
                return_value=result,
            ),
        ):
            response = self.client.get(
                "/api/cron/partner-sync",
                headers={"Authorization": "Bearer cron-test-secret"},
            )

        self.assertEqual(response.status_code, 500)

    def test_afternoon_routes_skip_cleanup_and_dispatch_embeddings(self) -> None:
        for source in ("partner", "police"):
            with self.subTest(source=source), patch.dict(os.environ, {"CRON_SECRET": "cron-test-secret"}), patch(
                "retriever_lost_found.web.cron.run_incremental_sync",
                return_value={"status": "succeeded"},
            ) as sync:
                self.dispatch.reset_mock()
                response = self.client.get(
                    f"/api/cron/{source}-sync-afternoon",
                    headers={"Authorization": "Bearer cron-test-secret"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["cache-control"], "no-store")
                sync.assert_called_once_with(source, cleanup_expired=False)
                self.dispatch.assert_called_once()

    def test_afternoon_routes_reject_unauthorized_requests_before_work(self) -> None:
        with patch.dict(os.environ, {"CRON_SECRET": "cron-test-secret"}), patch(
            "retriever_lost_found.web.cron.run_incremental_sync",
        ) as sync:
            for source in ("partner", "police"):
                for headers in ({}, {"Authorization": "Bearer invalid"}):
                    response = self.client.get(f"/api/cron/{source}-sync-afternoon", headers=headers)
                    self.assertEqual(response.status_code, 401)
            sync.assert_not_called()
            self.dispatch.assert_not_called()

    def test_afternoon_failure_reports_error_and_still_dispatches_missing_embeddings(self) -> None:
        with patch.dict(os.environ, {"CRON_SECRET": "cron-test-secret"}), patch(
            "retriever_lost_found.web.cron.run_incremental_sync", return_value={"status": "failed"},
        ):
            response = self.client.get(
                "/api/cron/partner-sync-afternoon", headers={"Authorization": "Bearer cron-test-secret"},
            )
        self.assertEqual(response.status_code, 500)
        self.dispatch.assert_called_once()

    def test_duplicate_afternoon_invocation_does_not_dispatch_embeddings(self) -> None:
        with patch.dict(os.environ, {"CRON_SECRET": "cron-test-secret"}), patch(
            "retriever_lost_found.web.cron.run_incremental_sync", return_value={"status": "already_running"},
        ):
            response = self.client.get(
                "/api/cron/partner-sync-afternoon", headers={"Authorization": "Bearer cron-test-secret"},
            )
        self.assertEqual(response.status_code, 500)
        self.dispatch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
