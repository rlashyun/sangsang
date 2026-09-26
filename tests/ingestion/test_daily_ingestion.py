from __future__ import annotations

import json
import unittest
from datetime import date
from unittest.mock import patch

from retriever_lost_found.ingestion import service as daily_ingestion
from retriever_lost_found.ingestion.collector import retention_start
from retriever_lost_found.ingestion.store import LocationReference, RunAlreadyActive


def selected_row(*, atc_id: str = "A1", storage: str = "테스트역") -> dict[str, str]:
    payload = {
        "atcId": atc_id,
        "fdSn": "1",
        "fdYmd": "2026-08-27",
        "prdtClNm": "지갑 > 남성용 지갑",
        "depPlace": storage,
    }
    return {
        "item_source_code": "partner_api",
        "atc_id": atc_id,
        "found_sequence": "1",
        "service_category_code": "wallet",
        "service_category_name": "지갑",
        "raw_category_name": "지갑 > 남성용 지갑",
        "color_name": "검정",
        "item_name": "지갑",
        "description": "검정 지갑",
        "image_url": "",
        "raw_storage_name": storage,
        "registered_on": "2026-08-27",
        "normalized_search_text": "지갑 검정",
        "raw_payload": json.dumps(payload, ensure_ascii=False),
    }


class FakeStore:
    secret_key = "test-secret"

    def __init__(self, *, already_running: bool = False, fail_upsert: bool = False) -> None:
        self.already_running = already_running
        self.fail_upsert = fail_upsert
        self.deleted = False
        self.upserted: list[dict] = []
        self.finished: list[dict] = []
        self.windows: list[tuple[date, date]] = []
        self.latest_date: date | None = None

    def latest_registered_on(self, source_code: str) -> date | None:
        return self.latest_date

    def create_run(
        self,
        source_code: str,
        start_date: date,
        end_date: date,
        *,
        trigger: str,
    ) -> int:
        if self.already_running:
            raise RunAlreadyActive(source_code)
        self.windows.append((start_date, end_date))
        return 7

    def load_category_ids(self, source_code: str) -> dict[str, int]:
        return {"지갑남성용지갑": 4}

    def load_aliases(self, source_code: str) -> dict[str, int]:
        return {"테스트역": 123}

    def load_locations(self, source_code: str) -> list[LocationReference]:
        return [LocationReference(123, "테스트역", "테스트역")]

    def existing_identities(self, source_code: str) -> set[tuple[str, str]]:
        return {("OLD", "1")}

    def upsert_found_items(self, rows: list[dict]) -> None:
        if self.fail_upsert:
            raise RuntimeError("upsert failed")
        self.upserted.extend(rows)

    def delete_expired(self, source_code: str) -> int:
        self.deleted = True
        return 3

    def finish_run(self, run_id: int, values: dict) -> None:
        self.finished.append(values)


class DailyIngestionTests(unittest.TestCase):
    @patch.object(daily_ingestion, "collect_selected_rows")
    @patch.object(daily_ingestion, "create_source_client")
    def test_success_upserts_before_deleting(self, create_client, collect_rows) -> None:
        store = FakeStore()
        collect_rows.return_value = (
            [selected_row()],
            {
                "fetched_count": 10,
                "selected_count": 1,
                "invalid_count": 0,
                "outside_range_count": 0,
                "api_total_count": 10,
            },
        )

        result = daily_ingestion._sync_source(
            store, source="partner", today=date(2026, 8, 27)
        )

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(result["deleted"], 3)
        self.assertTrue(store.deleted)
        self.assertEqual(store.upserted[0]["storage_location_id"], 123)
        self.assertEqual(store.upserted[0]["location_match_status"], "matched")
        self.assertEqual(store.windows, [(date(2026, 2, 27), date(2026, 8, 26))])
        self.assertEqual(store.finished[0]["status"], "succeeded")

    @patch.object(daily_ingestion, "collect_selected_rows")
    @patch.object(daily_ingestion, "create_source_client")
    def test_api_failure_never_deletes(self, create_client, collect_rows) -> None:
        store = FakeStore()
        collect_rows.side_effect = RuntimeError("API unavailable")

        result = daily_ingestion._sync_source(
            store, source="police", today=date(2026, 8, 27)
        )

        self.assertEqual(result["status"], "failed")
        self.assertFalse(store.deleted)
        self.assertEqual(store.finished[0]["deleted_count"], 0)
        self.assertEqual(store.windows, [(date(2026, 2, 27), date(2026, 8, 26))])

    @patch.object(daily_ingestion, "collect_selected_rows")
    @patch.object(daily_ingestion, "create_source_client")
    def test_upsert_failure_never_deletes(self, create_client, collect_rows) -> None:
        store = FakeStore(fail_upsert=True)
        collect_rows.return_value = (
            [selected_row()],
            {
                "fetched_count": 1,
                "selected_count": 1,
                "invalid_count": 0,
                "outside_range_count": 0,
                "api_total_count": 1,
            },
        )

        result = daily_ingestion._sync_source(
            store, source="partner", today=date(2026, 8, 27)
        )

        self.assertEqual(result["status"], "failed")
        self.assertFalse(store.deleted)
        self.assertEqual(store.finished[0]["inserted_count"], 0)

    def test_existing_running_row_prevents_second_run(self) -> None:
        store = FakeStore(already_running=True)

        result = daily_ingestion._sync_source(
            store, source="partner", today=date(2026, 8, 27)
        )

        self.assertEqual(result["status"], "already_running")
        self.assertFalse(store.deleted)

    def test_location_resolution_is_conservative(self) -> None:
        locations = [
            LocationReference(1, "서울강남경찰서", "서울강남경찰서"),
            LocationReference(2, "부산강남경찰서", "부산강남경찰서"),
        ]
        self.assertEqual(
            daily_ingestion._resolve_location("서울강남경찰서", {}, locations),
            (1, "matched"),
        )
        self.assertEqual(
            daily_ingestion._resolve_location("강남경찰서", {}, locations),
            (None, "ambiguous"),
        )

    @patch.object(daily_ingestion, "collect_selected_rows")
    @patch.object(daily_ingestion, "create_source_client")
    def test_incremental_sync_refetches_latest_stored_date(
        self, create_client, collect_rows
    ) -> None:
        store = FakeStore()
        store.latest_date = date(2026, 8, 25)
        collect_rows.return_value = (
            [],
            {
                "fetched_count": 0,
                "selected_count": 0,
                "invalid_count": 0,
                "outside_range_count": 0,
                "api_total_count": 0,
            },
        )

        result = daily_ingestion._sync_source(
            store, source="partner", today=date(2026, 8, 27)
        )

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(store.windows, [(date(2026, 8, 25), date(2026, 8, 26))])

    @patch.object(daily_ingestion, "collect_selected_rows")
    @patch.object(daily_ingestion, "create_source_client")
    def test_cron_incremental_sync_refetches_latest_stored_date(
        self, create_client, collect_rows
    ) -> None:
        store = FakeStore()
        store.latest_date = date(2026, 8, 25)
        collect_rows.return_value = (
            [],
            {
                "fetched_count": 0,
                "selected_count": 0,
                "invalid_count": 0,
                "outside_range_count": 0,
                "api_total_count": 0,
            },
        )

        result = daily_ingestion._sync_source(
            store,
            source="partner",
            today=date(2026, 8, 27),
            incremental_only=True,
        )

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(store.windows, [(date(2026, 8, 25), date(2026, 8, 26))])

    @patch.object(daily_ingestion, "collect_selected_rows")
    @patch.object(daily_ingestion, "create_source_client")
    def test_cron_refetches_yesterday_when_database_is_current(
        self, create_client, collect_rows
    ) -> None:
        store = FakeStore()
        store.latest_date = date(2026, 8, 27)
        collect_rows.return_value = (
            [],
            {
                "fetched_count": 0,
                "selected_count": 0,
                "invalid_count": 0,
                "outside_range_count": 0,
                "api_total_count": 0,
            },
        )

        result = daily_ingestion._sync_source(
            store,
            source="partner",
            today=date(2026, 8, 27),
            incremental_only=True,
        )

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(store.windows, [(date(2026, 8, 26), date(2026, 8, 26))])

    def test_calendar_six_month_window_includes_march_26_not_today(self) -> None:
        self.assertEqual(retention_start(date(2026, 9, 26)), date(2026, 3, 26))
        self.assertEqual(retention_start(date(2026, 8, 31)), date(2026, 2, 28))
        self.assertEqual(retention_start(date(2028, 8, 31)), date(2028, 2, 29))

        store = FakeStore()
        with patch.object(daily_ingestion, "create_source_client"), patch.object(
            daily_ingestion, "collect_selected_rows"
        ) as collect_rows:
            collect_rows.return_value = (
                [],
                {
                    "fetched_count": 0, "selected_count": 0,
                    "invalid_count": 0, "outside_range_count": 0,
                    "api_total_count": 0,
                },
            )
            daily_ingestion._sync_source(store, source="partner", today=date(2026, 9, 26))
        self.assertEqual(store.windows, [(date(2026, 3, 26), date(2026, 9, 25))])


if __name__ == "__main__":
    unittest.main()
