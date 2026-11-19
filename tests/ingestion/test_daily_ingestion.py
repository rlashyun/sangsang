from __future__ import annotations

import json
import unittest
from datetime import date, datetime
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
        cleanup_expired: bool = True,
        business_date: date | None = None,
    ) -> int:
        if self.already_running:
            raise RunAlreadyActive(source_code)
        self.windows.append((start_date, end_date))
        self.run_options = {"trigger": trigger, "cleanup_expired": cleanup_expired,
                            "business_date": business_date or end_date}
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

    def upsert_found_items(self, rows: list[dict], *, statistics: dict | None = None) -> int:
        if self.fail_upsert:
            raise RuntimeError("upsert failed")
        self.upserted.extend(rows)
        if statistics is not None:
            statistics["excluded_overseas"] = sum(row["location_match_status"] == "excluded_overseas" for row in rows)
        return sum(row["location_match_status"] in {"unmatched", "ambiguous"} for row in rows)

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
        self.assertEqual(store.windows, [(date(2026, 2, 27), date(2026, 8, 27))])
        self.assertEqual(collect_rows.call_args.kwargs["end_date"], date(2026, 8, 27))
        self.assertEqual(store.finished[0]["metadata"]["business_date"], "2026-08-27")
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
        self.assertEqual(store.windows, [(date(2026, 2, 27), date(2026, 8, 27))])

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
        store.upsert_found_items = lambda rows, **kwargs: 0  # Verified DB trigger resolves the incoming row.
        collect_rows.return_value = ([selected_row(storage="unknown")], {
            "fetched_count": 1, "selected_count": 1, "invalid_count": 0,
            "outside_range_count": 0, "api_total_count": 1,
        })
        result = daily_ingestion._sync_source(store, source="partner", today=date(2026, 10, 7))
        self.assertEqual(result["status"], "succeeded")
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
        self.assertEqual(store.windows, [(date(2026, 8, 25), date(2026, 8, 27))])

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
        self.assertEqual(store.windows, [(date(2026, 8, 25), date(2026, 8, 27))])

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
        self.assertEqual(store.windows, [(date(2026, 8, 26), date(2026, 8, 27))])

    def test_calendar_six_month_window_includes_march_26_through_today(self) -> None:
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
        self.assertEqual(store.windows, [(date(2026, 3, 26), date(2026, 9, 26))])

    @patch.object(daily_ingestion, "create_source_client")
    def test_afternoon_ingests_today_without_deleting_expired_items(self, create_client) -> None:
        store = FakeStore()
        store.latest_date = date(2026, 8, 27)
        create_client.return_value.fetch_page.return_value = ([
            {"atcId": "TODAY", "fdSn": "1", "fdYmd": "2026-08-27",
             "prdtClNm": "지갑 > 남성용 지갑", "depPlace": "테스트역", "fdPrdtNm": "지갑"},
            {"atcId": "LATE", "fdSn": "1", "fdYmd": "2026-08-26",
             "prdtClNm": "지갑 > 남성용 지갑", "depPlace": "테스트역", "fdPrdtNm": "지갑"},
            {"atcId": "FUTURE", "fdSn": "1", "fdYmd": "2026-08-28",
             "prdtClNm": "지갑 > 남성용 지갑", "depPlace": "테스트역", "fdPrdtNm": "지갑"},
        ], 3)

        result = daily_ingestion._sync_source(
            store, source="partner", today=date(2026, 8, 27),
            incremental_only=True, cleanup_expired=False,
        )

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual({row["atc_id"] for row in store.upserted}, {"TODAY", "LATE"})
        self.assertFalse(store.deleted)
        self.assertEqual(result["deleted"], 0)
        metadata = store.finished[0]["metadata"]
        self.assertEqual(metadata["business_date"], "2026-08-27")
        self.assertFalse(metadata["cleanup_enabled"])
        filters = create_client.return_value.fetch_page.call_args.kwargs
        self.assertEqual(filters, {"START_YMD": "20260826", "END_YMD": "20260827"})


def category_row(raw_category: str, atc_id: str) -> dict[str, str]:
    row = selected_row(atc_id=atc_id)
    row["raw_category_name"] = raw_category
    return row


class CategoryResolutionTests(unittest.TestCase):
    # PRD 2.2, 4.5 — 2026-11-17 전체 물품 확대: 정확 매핑 → 상위분류 → 기타물품.
    IDS = {"지갑남성용지갑": 4, "지갑": 99, "전자기기": 3, "기타물품": 8}

    def build(self, raw_category: str, statistics: dict | None = None) -> dict:
        return daily_ingestion._build_database_rows(
            [category_row(raw_category, "C1")],
            source_code="partner_api",
            category_ids=self.IDS,
            aliases={"테스트역": 123},
            locations=[],
            statistics=statistics,
        )[0]

    def test_exact_mapping_wins_over_parent(self) -> None:
        self.assertEqual(self.build("지갑 > 남성용 지갑")["category_id"], 4)

    def test_parent_is_used_when_pair_is_unmapped(self) -> None:
        self.assertEqual(self.build("전자기기 > 스마트워치")["category_id"], 3)
        self.assertEqual(self.build("지갑 > 새 지갑")["category_id"], 99)

    def test_unknown_parent_falls_back_and_is_counted(self) -> None:
        statistics: dict = {}
        row = self.build("반려동물 > 강아지", statistics)
        self.assertEqual(row["category_id"], 8)
        self.assertEqual(row["raw_category_name"], "반려동물 > 강아지")
        self.assertEqual(statistics["category_fallback"], 1)

        statistics = {}
        self.build("전자기기 > 스마트워치", statistics)
        self.assertEqual(statistics["category_fallback"], 0)

    def test_missing_fallback_category_fails_loudly(self) -> None:
        # 마이그레이션 전 DB(6개 분류뿐)에 새 앱이 붙으면 수집을 실패시켜 만료 삭제를 막는다.
        with self.assertRaisesRegex(RuntimeError, "카테고리"):
            daily_ingestion._build_database_rows(
                [category_row("현금 > 현금", "C1")],
                source_code="partner_api",
                category_ids={"지갑남성용지갑": 4},
                aliases={},
                locations=[],
            )

    @patch.object(daily_ingestion, "collect_selected_rows")
    @patch.object(daily_ingestion, "create_source_client")
    def test_fallback_count_is_audited(self, create_client, collect_rows) -> None:
        store = FakeStore()
        store.load_category_ids = lambda source_code: dict(self.IDS)
        collect_rows.return_value = (
            [category_row("반려동물 > 강아지", "C1"), selected_row(atc_id="C2")],
            {
                "fetched_count": 2, "selected_count": 2, "invalid_count": 0,
                "outside_range_count": 0, "api_total_count": 2,
            },
        )

        result = daily_ingestion._sync_source(store, source="partner", today=date(2026, 8, 27))

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual({row["category_id"] for row in store.upserted}, {8, 4})
        self.assertEqual(store.finished[0]["metadata"]["category_fallback_count"], 1)


def fetch_summary(count: int) -> dict[str, int]:
    return {"fetched_count": count, "selected_count": count, "invalid_count": 0,
            "outside_range_count": 0, "api_total_count": count}


class BackfillTests(unittest.TestCase):
    # PRD 4.5 — 2026-11-20 전체 물품 확대 뒤 과거 6개월의 새 분류 물품을 채우는 수동 백필.
    TODAY = date(2026, 11, 20)

    def test_windows_split_by_chunk_and_stay_inside_retention(self) -> None:
        windows = daily_ingestion._backfill_windows(
            date(2026, 5, 20), date(2026, 6, 5), today=self.TODAY, source="partner", chunk_days=7,
        )
        self.assertEqual(windows, [
            (date(2026, 5, 20), date(2026, 5, 26)),
            (date(2026, 5, 27), date(2026, 6, 2)),
            (date(2026, 6, 3), date(2026, 6, 5)),
        ])
        for start, end in (
            (date(2026, 6, 5), date(2026, 6, 4)),    # 시작일이 종료일보다 늦음
            (date(2026, 5, 19), date(2026, 6, 4)),   # 보존기간 시작일(5-20) 이전
            (date(2026, 11, 19), date(2026, 11, 21)),  # 오늘 이후
        ):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                daily_ingestion._backfill_windows(start, end, today=self.TODAY, source="partner")
        with self.assertRaises(ValueError):
            daily_ingestion._backfill_windows(
                date(2026, 6, 1), date(2026, 6, 2), today=self.TODAY, source="partner", chunk_days=0,
            )

    def test_cron_hours_block_new_chunks(self) -> None:
        # Vercel Hobby 크론은 09·21시대 수집, 11·23시대 임베딩·알림. 한 출처의 running은 하나뿐이다.
        kst = daily_ingestion.KOREA_TIMEZONE
        blocked = {(8, 40), (9, 30), (10, 59), (11, 0), (20, 45), (21, 59), (22, 40), (23, 10)}
        allowed = {(0, 30), (8, 39), (10, 0), (10, 39), (12, 0), (20, 39), (22, 39)}
        for hour, minute in blocked | allowed:
            with self.subTest(time=f"{hour:02d}:{minute:02d}"):
                now = datetime(2026, 11, 20, hour, minute, tzinfo=kst)
                self.assertEqual(daily_ingestion._backfill_blocked(now), (hour, minute) in blocked)

    @patch.object(daily_ingestion, "collect_selected_rows")
    @patch.object(daily_ingestion, "create_source_client")
    def test_backfill_inserts_only_new_rows(self, create_client, collect_rows) -> None:
        collect_rows.return_value = ([selected_row(atc_id="OLD"), selected_row(atc_id="NEW")], fetch_summary(2))

        store = FakeStore()
        result = daily_ingestion._sync_source(
            store, source="partner", today=self.TODAY,
            backfill_window=(date(2026, 6, 1), date(2026, 6, 7)),
        )

        self.assertEqual(result["status"], "succeeded")
        # 이미 있는 물품은 다시 저장하지 않아 기존 위치·상세정보를 건드리지 않는다.
        self.assertEqual([row["atc_id"] for row in store.upserted], ["NEW"])
        self.assertEqual((result["inserted"], result["updated"]), (1, 0))
        self.assertEqual(collect_rows.call_args.kwargs["start_date"], date(2026, 6, 1))
        self.assertEqual(collect_rows.call_args.kwargs["end_date"], date(2026, 6, 7))
        self.assertEqual(store.windows, [(date(2026, 6, 1), date(2026, 6, 7))])
        # 임베딩 회차 판정이 오늘 기준을 유지하도록 business_date는 오늘, 정리 허용 회차로 기록한다.
        self.assertEqual(store.run_options, {"trigger": "manual_backfill", "cleanup_expired": True,
                                             "business_date": self.TODAY})
        self.assertEqual(store.finished[0]["metadata"]["skipped_existing_count"], 1)

        # 일반 동기화는 같은 입력에서 기존 물품도 다시 저장한다(멱등 upsert).
        regular = FakeStore()
        daily_ingestion._sync_source(regular, source="partner", today=self.TODAY)
        self.assertEqual(sorted(row["atc_id"] for row in regular.upserted), ["NEW", "OLD"])

    @patch.object(daily_ingestion, "_enrich_ambiguous_rows", return_value={"eligible": 1})
    @patch.object(daily_ingestion, "collect_selected_rows")
    @patch.object(daily_ingestion, "create_source_client")
    def test_backfill_detail_budget_starts_at_lookup_and_skips_existing(
        self, create_client, collect_rows, enrich,
    ) -> None:
        collect_rows.return_value = (
            [selected_row(atc_id="OLD", storage="중앙지구대"), selected_row(atc_id="NEW", storage="중앙지구대")],
            fetch_summary(2),
        )
        store = FakeStore()
        store.load_aliases = lambda source_code: {}
        store.load_locations = lambda source_code: [
            LocationReference(1, "서울중앙지구대", "서울중앙지구대"),
            LocationReference(2, "충주중앙지구대", "충주중앙지구대"),
        ]
        store.load_detail_mappings = lambda source_code, active_ids=None: {}
        clock = iter([0.0])  # 실행 시작만 0초, 그 뒤는 1,000초 — 긴 백필 구간의 상세 조회 시점

        with patch.object(daily_ingestion.time, "monotonic", lambda: next(clock, 1000.0)):
            daily_ingestion._sync_source(
                store, source="police", today=self.TODAY,
                backfill_window=(date(2026, 6, 1), date(2026, 6, 7)),
            )

        rows = enrich.call_args.args[0]
        self.assertEqual([row["atc_id"] for row in rows], ["NEW"])
        self.assertEqual(enrich.call_args.kwargs["deadline"], 1000.0 + daily_ingestion.DETAIL_BUDGET_SECONDS)

    def test_run_backfill_stops_on_failure_with_resume_point(self) -> None:
        results = iter(["succeeded", "failed", "succeeded"])
        calls = []

        def fake_sync(store, *, source, today, backfill_window):
            calls.append((source, backfill_window))
            return {**daily_ingestion._empty_result("partner_api", next(results)),
                    "inserted": 3, "skipped_existing": 2}

        allowed = datetime(2026, 11, 20, 13, 0, tzinfo=daily_ingestion.KOREA_TIMEZONE)
        with patch.object(daily_ingestion, "_sync_source", side_effect=fake_sync):
            summary = daily_ingestion.run_backfill(
                FakeStore(), sources=("partner",), start=date(2026, 6, 1), end=date(2026, 6, 21),
                today=self.TODAY, chunk_days=7, now=lambda: allowed,
            )

        self.assertEqual(summary["status"], "failed")
        self.assertEqual(len(calls), 2)
        self.assertEqual(summary["resume"], {"source": "partner", "from": "2026-06-08"})
        self.assertEqual(summary["inserted"], 6)

    def test_run_backfill_does_not_start_inside_cron_hours(self) -> None:
        blocked = datetime(2026, 11, 20, 21, 10, tzinfo=daily_ingestion.KOREA_TIMEZONE)
        with patch.object(daily_ingestion, "_sync_source") as sync:
            summary = daily_ingestion.run_backfill(
                FakeStore(), sources=("partner", "police"), start=date(2026, 6, 1), end=date(2026, 6, 7),
                today=self.TODAY, now=lambda: blocked,
            )
        sync.assert_not_called()
        self.assertEqual(summary["status"], "stopped")
        self.assertEqual(summary["resume"], {"source": "partner", "from": "2026-06-01"})

    def test_run_backfill_runs_every_chunk_for_each_source(self) -> None:
        allowed = datetime(2026, 11, 20, 13, 0, tzinfo=daily_ingestion.KOREA_TIMEZONE)
        with patch.object(daily_ingestion, "_sync_source",
                          return_value=daily_ingestion._empty_result("x", "succeeded")) as sync:
            summary = daily_ingestion.run_backfill(
                FakeStore(), sources=("partner", "police"), start=date(2026, 6, 1), end=date(2026, 6, 10),
                today=self.TODAY, chunk_days=7, now=lambda: allowed,
            )
        self.assertEqual(summary["status"], "succeeded")
        self.assertEqual([(call.kwargs["source"], call.kwargs["backfill_window"][0]) for call in sync.call_args_list], [
            ("partner", date(2026, 6, 1)), ("partner", date(2026, 6, 8)),
            ("police", date(2026, 6, 1)), ("police", date(2026, 6, 8)),
        ])
        self.assertNotIn("resume", summary)


if __name__ == "__main__":
    unittest.main()
