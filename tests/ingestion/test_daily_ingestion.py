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

    def load_detail_mappings(self, source_code: str) -> dict[tuple[str, str], int]:
        raise AssertionError("ingestion must leave verified detail mapping to the DB")

    def load_detail_evidence(self, source_code: str) -> dict[tuple[str, str], dict]:
        raise RuntimeError("57014: canceling statement due to statement timeout")

    def load_ambiguous_items(self, source_code: str) -> list[dict]:
        raise AssertionError("ingestion must not automatically process the old backlog")

    def existing_identities(self, source_code: str, identities: set[tuple[str, str]]) -> set[tuple[str, str]]:
        self.requested_identities = identities
        return {("OLD", "1")} & identities

    def upsert_found_items(self, rows: list[dict]) -> int:
        if self.fail_upsert:
            raise RuntimeError("upsert failed")
        self.upserted.extend(rows)
        return sum(row["location_match_status"] != "matched" for row in rows)

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

    def test_full_name_precedes_parenthesis_base_and_retains_real_ambiguity(self) -> None:
        locations = [LocationReference(1, "비발디파크", "비발디파크"),
                     LocationReference(2, "비발디파크(오크동)", "비발디파크오크동")]
        resolver = daily_ingestion._LocationResolver({}, locations)
        self.assertEqual(resolver.resolve("비발디파크"), (1, "matched"))
        self.assertEqual(resolver.resolve("비발디파크(오크동)"), (2, "matched"))
        self.assertEqual(resolver.resolve("비발디"), (None, "unmatched"))
        locations.append(LocationReference(3, "비발디파크", "비발디파크"))
        self.assertEqual(daily_ingestion._resolve_location("비발디파크", {}, locations),
                         (None, "ambiguous"))

    def test_base_and_suffix_fallbacks_and_active_manual_exception(self) -> None:
        locations = [LocationReference(1, "서울테스트역(안내소)", "서울테스트역안내소"),
                     LocationReference(2, "다른역", "다른역")]
        resolver = daily_ingestion._LocationResolver({"다른역": 1, "폐쇄기관": 999}, locations)
        self.assertEqual(resolver.resolve("서울테스트역"), (1, "matched"))
        self.assertEqual(resolver.resolve("테스트역안내소"), (1, "matched"))
        self.assertEqual(resolver.resolve("다른역"), (1, "matched"))
        self.assertEqual(resolver.resolve("폐쇄기관"), (None, "unmatched"))
        self.assertEqual(resolver.resolve(""), (None, "unmatched"))

    def test_repeated_names_do_not_rescan_location_catalog(self) -> None:
        class SinglePass(list):
            def __iter__(self):
                if getattr(self, "consumed", False):
                    raise AssertionError("기관 원장을 반복 조회함")
                self.consumed = True
                return super().__iter__()

        resolver = daily_ingestion._LocationResolver({}, [LocationReference(1, "서울테스트역", "서울테스트역")])
        resolver.suffix = SinglePass(resolver.suffix)
        for _ in range(100):
            self.assertEqual(resolver.resolve("테스트역"), (1, "matched"))

    @patch("urllib.request.urlopen", side_effect=AssertionError("detail API must not run"))
    def test_unresolved_name_never_fetches_details_and_unique_name_still_matches(self, network) -> None:
        rows = daily_ingestion._build_database_rows(
            [selected_row(storage="중앙지구대"), selected_row(atc_id="A2", storage="서울중앙지구대")],
            source_code="police_api",
            category_ids={"지갑남성용지갑": 4}, aliases={},
            locations=[LocationReference(1, "서울중앙지구대", "서울중앙지구대"),
                       LocationReference(2, "충주중앙지구대", "충주중앙지구대")],
        )
        self.assertIsNone(rows[0]["storage_location_id"])
        self.assertEqual(rows[0]["location_match_status"], "ambiguous")
        self.assertIsNone(rows[0]["detail_org_id"])
        self.assertEqual(rows[1]["storage_location_id"], 1)
        self.assertEqual(rows[1]["location_match_status"], "matched")
        network.assert_not_called()

    @patch.object(daily_ingestion, "collect_selected_rows")
    @patch.object(daily_ingestion, "create_source_client")
    def test_removed_detail_timeout_cannot_block_daily_sync(self, create_client, collect_rows) -> None:
        # FakeStore raises today's 57014 if the removed full-table detail lookup is called.
        store = FakeStore()
        collect_rows.return_value = ([selected_row()], {
            "fetched_count": 1, "selected_count": 1, "invalid_count": 0,
            "outside_range_count": 0, "api_total_count": 1,
        })
        result = daily_ingestion._sync_source(store, source="partner", today=date(2026, 10, 7))
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(store.requested_identities, {("A1", "1")})
        self.assertTrue(store.deleted)

    @patch.object(daily_ingestion, "collect_selected_rows")
    @patch.object(daily_ingestion, "create_source_client")
    def test_audit_counts_final_db_matches(self, create_client, collect_rows) -> None:
        store = FakeStore()
        store.upsert_found_items = lambda rows: 0  # Verified DB trigger resolves the incoming row.
        collect_rows.return_value = ([selected_row(storage="unknown")], {
            "fetched_count": 1, "selected_count": 1, "invalid_count": 0,
            "outside_range_count": 0, "api_total_count": 1,
        })
        result = daily_ingestion._sync_source(store, source="partner", today=date(2026, 10, 7))
        self.assertEqual(result["unmatched"], 0)
        self.assertEqual(store.finished[0]["unmatched_count"], 0)

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
