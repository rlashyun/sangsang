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
        sync.assert_called_once_with("partner")

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
        sync.assert_called_once_with("police")

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


if __name__ == "__main__":
    unittest.main()
