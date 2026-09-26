from __future__ import annotations

import unittest
from pathlib import Path


MIGRATIONS = Path(__file__).resolve().parents[2] / "supabase" / "migrations"


class SchemaMigrationTests(unittest.TestCase):
    def test_core_schema_and_runtime_contract_are_reproducible(self) -> None:
        baseline = (
            MIGRATIONS / "20260826133434_create_lost_found_core_schema.sql"
        ).read_text(encoding="utf-8")
        hardening = (
            MIGRATIONS / "20260827231537_harden_runtime_database_contracts.sql"
        ).read_text(encoding="utf-8")

        for table in (
            "item_sources",
            "location_sources",
            "storage_locations",
            "storage_location_aliases",
            "item_categories",
            "category_mappings",
            "found_items",
            "ingestion_runs",
        ):
            self.assertIn(f"create table public.{table}", baseline.lower())

        self.assertIn("map_locations_with_item_counts", hardening)
        self.assertIn("ingestion_runs_one_running_per_source", hardening)
        self.assertIn("found_items_set_seen_at", hardening)
        self.assertIn("security invoker", hardening.lower())
        self.assertIn("grant execute", hardening.lower())

    def test_retention_delete_is_batched_and_service_role_only(self) -> None:
        # PRD 4.5 — 전량 단일 DELETE는 statement_timeout(57014)에 걸린다.
        # 줄바꿈에 묶이지 않도록 공백을 정규화하고 비교한다.
        batched = " ".join(
            (MIGRATIONS / "20260921011500_batch_delete_expired_found_items.sql")
            .read_text(encoding="utf-8")
            .lower()
            .split()
        )

        # 옛 단일 인자 함수를 남기면 오버로드가 되어 호출이 모호해진다.
        self.assertIn(
            "drop function if exists public.delete_expired_found_items(text)", batched
        )
        self.assertIn("p_limit", batched)
        self.assertIn("security invoker", batched)
        # 절대규칙 6 — service_role 전용, anon/authenticated 접근 금지.
        self.assertIn(
            "revoke all on function public.delete_expired_found_items(text, integer)"
            " from public, anon, authenticated",
            batched,
        )
        self.assertIn(
            "grant execute on function"
            " public.delete_expired_found_items(text, integer) to service_role",
            batched,
        )

    def test_retention_uses_six_calendar_months(self) -> None:
        migration = (
            MIGRATIONS / "20260925163815_calendar_six_month_retention.sql"
        ).read_text(encoding="utf-8").lower()
        korea_date_fix = (
            MIGRATIONS / "20260925164317_use_korea_date_for_retention.sql"
        ).read_text(encoding="utf-8").lower()
        self.assertIn("rename column retention_days to retention_months", migration)
        self.assertIn("set retention_months = 6", migration)
        self.assertIn("make_interval(months => ts.retention_months", korea_date_fix)
        self.assertIn("now() at time zone 'asia/seoul'", korea_date_fix)


if __name__ == "__main__":
    unittest.main()
