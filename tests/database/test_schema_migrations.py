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

    def test_map_rpc_aggregates_counts_before_join_and_is_service_role_only(self) -> None:
        # PRD 6 — 행마다 기관 컬럼(geography 포함)으로 group by하면 57014로 지도 전체가 비었다.
        migration = " ".join(
            (MIGRATIONS / "20260927090000_fast_map_location_rpcs.sql")
            .read_text(encoding="utf-8")
            .lower()
            .split()
        )

        self.assertIn(
            "create or replace function public.map_locations_with_item_counts()",
            migration,
        )
        self.assertIn("item_counts as materialized", migration)
        self.assertIn("group by item.storage_location_id", migration)
        self.assertNotIn("storage.position,", migration.split("group by", 1)[1])
        self.assertIn("create or replace function public.map_locations()", migration)
        for function in ("map_locations_with_item_counts()", "map_locations()"):
            self.assertIn(
                f"revoke all on function public.{function} from public, anon, authenticated",
                migration,
            )
            self.assertIn(
                f"grant execute on function public.{function} to service_role",
                migration,
            )

    def test_detail_mapping_evidence_is_service_role_only(self) -> None:
        migration = (
            MIGRATIONS / "20261002091648_verified_ambiguous_location_mapping.sql"
        ).read_text(encoding="utf-8").lower()
        self.assertIn("add column detail_org_id text", migration)
        self.assertIn("create table public.storage_location_detail_mappings", migration)
        self.assertIn("primary key (item_source_code, normalized_raw_storage_name, detail_org_id)", migration)
        self.assertIn("enable row level security", migration)
        self.assertIn("from public, anon, authenticated", migration)
        self.assertIn("create trigger found_items_resolve_verified_detail", migration)
        self.assertIn("security invoker set search_path = ''", migration)
        self.assertIn("new.raw_storage_name is distinct from old.raw_storage_name", migration)

    def test_found_items_page_rpc_reads_newest_rows_per_location_and_is_service_role_only(
        self,
    ) -> None:
        # PRD 3.3 · 5 — 3km 후보 3.7만 건(26MB)을 1,000건씩 38번 받던 조회를 한 페이지로 줄인다.
        migration = " ".join(
            (MIGRATIONS / "20261105090000_paged_found_items_rpc.sql")
            .read_text(encoding="utf-8")
            .lower()
            .split()
        )
        signature = "public.found_items_page(bigint[], integer, date, bigint, boolean)"

        # 기관별 최신순 인덱스가 있어야 기관마다 앞쪽 몇 건만 읽고 멈출 수 있다.
        self.assertIn(
            "on public.found_items (storage_location_id, registered_on desc, id desc)",
            migration,
        )
        self.assertIn("create or replace function public.found_items_page(", migration)
        # 기관마다 limit만큼만 읽은 뒤 합친다 — 전체 후보를 정렬하지 않는다.
        self.assertIn("cross join lateral", migration)
        # 커서 조건에 "p_before_registered_on is null or ..."를 쓰면 함수의 일반 계획에서
        # 인덱스 조건이 아니라 필터가 되어, 뒤 페이지일수록 앞쪽 행을 다 훑는다(2026-11-05 로컬 계획 확인).
        self.assertIn(
            "(item.registered_on, item.id) < ( coalesce(p_before_registered_on, 'infinity'::date),",
            migration,
        )
        self.assertNotIn("p_before_registered_on is null", migration)
        self.assertIn("security invoker", migration)
        self.assertIn(
            f"revoke all on function {signature} from public, anon, authenticated",
            migration,
        )
        self.assertIn(f"grant execute on function {signature} to service_role", migration)

    def _alert_migration(self) -> str:
        return " ".join(
            (MIGRATIONS / "20261106120000_create_alert_scheduler.sql")
            .read_text(encoding="utf-8")
            .lower()
            .split()
        )

    def test_alert_scheduler_tables_are_service_role_only(self) -> None:
        # 절대 규칙 6 · 설계 4장 — 알림 예약 테이블도 RLS를 켜고 service_role만 접근한다.
        migration = self._alert_migration()
        tables = (
            "alert_reservations",
            "alert_match_cursors",
            "alert_messages",
            "alert_notified_items",
            "alert_runs",
        )
        for table in tables:
            self.assertIn(f"create table public.{table} (", migration)
            self.assertIn(f"alter table public.{table} enable row level security", migration)
        table_list = ", ".join(f"public.{table}" for table in tables)
        self.assertIn(f"revoke all on table {table_list} from public, anon, authenticated", migration)
        self.assertIn(
            f"grant select, insert, update, delete on table {table_list} to service_role",
            migration,
        )
        sequences = (
            "public.alert_reservations_id_seq, public.alert_messages_id_seq,"
            " public.alert_runs_id_seq"
        )
        self.assertIn(f"revoke all on sequence {sequences} from public, anon, authenticated", migration)
        self.assertIn(f"grant usage, select on sequence {sequences} to service_role", migration)

    def test_alert_scheduler_functions_are_service_role_only(self) -> None:
        migration = self._alert_migration()
        signatures = (
            "create_alert_reservation(text, text, text, double precision, double precision,"
            " integer, text, text, timestamptz, integer, integer)",
            "confirm_alert_reservation(bigint)",
            "extend_alert_reservation(bigint, integer)",
            "cancel_alert_reservation(bigint)",
            "alert_match_queue(text)",
            "alert_candidate_items(bigint, text)",
            "record_alert_match(bigint, text, timestamptz, jsonb)",
            "enqueue_alert_expiry_notices(interval)",
            "claim_alert_messages(integer, integer, bigint)",
            "alert_message_payload(bigint)",
            "finish_alert_message(bigint, text, text)",
            "close_ended_alert_reservations()",
        )
        for signature in signatures:
            self.assertIn(
                f"revoke all on function public.{signature} from public, anon, authenticated",
                migration,
            )
            self.assertIn(f"grant execute on function public.{signature} to service_role", migration)
        # 함수마다 한 번씩 — 주석에 같은 문구를 쓰지 않는다.
        self.assertEqual(migration.count("security invoker"), len(signatures))
        self.assertEqual(migration.count("set search_path = ''"), len(signatures))

    def test_alert_scheduler_keeps_its_rules_in_the_database(self) -> None:
        migration = self._alert_migration()
        # 설계 8장 — 끝난 예약에는 이메일·IP 해시가 남을 수 없다.
        self.assertIn(
            "status in ('expired', 'cancelled', 'unconfirmed_expired') and email is null"
            " and ip_hash is null",
            migration,
        )
        # 설계 4.4 — 한 번 알린 물품은 다시 들어갈 수 없다.
        self.assertIn("primary key (reservation_id, found_item_id)", migration)
        # 설계 4.3 — 예약마다 대기 알림 메일 1통, 같은 만료일 안내 1통, 확인 메일 1통
        self.assertIn(
            "on public.alert_messages (reservation_id) where kind = 'match' and status = 'pending'",
            migration,
        )
        self.assertIn(
            "on public.alert_messages (reservation_id, expiry_for) where kind = 'expiry_notice'",
            migration,
        )
        self.assertIn("on public.alert_messages (reservation_id) where kind = 'confirm'", migration)
        # PRD 4.1 — 화면의 Haversine과 경계를 맞추는 구면 계산
        self.assertIn("reservation.radius_m, false", migration)
        # 절대 규칙 5 — 반경 안 기관 id와 같은 물품만 후보가 된다(미매칭 NULL은 빠진다).
        self.assertIn("join scope on scope.id = item.storage_location_id", migration)
        # 커서 이후 새 물품을 바로 찾는 인덱스, 보존기간 삭제가 cascade로 이 표를 찾는 인덱스
        self.assertIn("on public.found_items (item_source_code, first_seen_at)", migration)
        self.assertIn("on public.alert_notified_items (found_item_id)", migration)
        # 설계 4.6 — 450통 계산이 동시 실행에서도 맞도록 잠근다.
        self.assertIn("pg_advisory_xact_lock(pg_catalog.hashtext('alert_messages_claim'))", migration)
        self.assertIn("for update skip locked", migration)


if __name__ == "__main__":
    unittest.main()
