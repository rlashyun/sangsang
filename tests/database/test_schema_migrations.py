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
            (MIGRATIONS / "20261115090000_create_alert_scheduler.sql")
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
            "alert_request_log",
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
        # 설계 8장 — 끝난 예약에는 이메일이 남을 수 없다.
        self.assertIn(
            "status in ('expired', 'cancelled', 'unconfirmed_expired') and email is null)",
            migration,
        )
        # 설계 14장 — IP 제한은 예약 행이 아니라 24시간 기록으로 센다. 취소해도 제한이 풀리지 않고,
        # IP 해시는 24시간이 지나면 지운다.
        self.assertNotIn("ip_hash = null", migration)
        self.assertIn("from public.alert_request_log as request where request.ip_hash = p_ip_hash", migration)
        self.assertIn(
            "delete from public.alert_request_log where created_at <= now() - interval '24 hours'",
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
        # PRD 4.3-E — 알림 반경은 1·1.5·2km, 비교는 D+1부터, 종료 안내는 마지막 날 실행에서만(24시간 창)
        # 그날 알림 메일과 한 통으로 보낸다.
        self.assertIn("check (radius_m in (1000, 1500, 2000))", migration)
        self.assertIn(
            "(reservation.created_at at time zone 'asia/seoul')::date"
            " < (now() at time zone 'asia/seoul')::date",
            migration,
        )
        self.assertIn("p_window interval default interval '24 hours'", migration)
        self.assertIn("set kind = 'expiry_notice', expiry_for = reservation.expires_at", migration)
        # 설계 4.6 — 450통 계산이 동시 실행에서도 맞도록 잠근다.
        self.assertIn("pg_advisory_xact_lock(pg_catalog.hashtext('alert_messages_claim'))", migration)
        self.assertIn("for update skip locked", migration)

    def _alert_places_migration(self) -> str:
        return " ".join(
            (MIGRATIONS / "20261115130000_alert_reservation_places.sql")
            .read_text(encoding="utf-8")
            .lower()
            .split()
        )

    def test_alert_places_table_is_service_role_only(self) -> None:
        # 절대 규칙 6 · 설계 16장 — 장소 표도 RLS를 켜고 service_role만 접근한다.
        migration = self._alert_places_migration()
        self.assertIn("create table public.alert_reservation_places (", migration)
        self.assertIn("alter table public.alert_reservation_places enable row level security", migration)
        self.assertIn(
            "revoke all on table public.alert_reservation_places from public, anon, authenticated", migration
        )
        self.assertIn(
            "grant select, insert, update, delete on table public.alert_reservation_places to service_role",
            migration,
        )
        # 예약을 지우면(확인 메일 실패 등) 장소도 지운다. 이메일만 지우는 종료·취소에서는 남아 통계에 쓰인다.
        self.assertIn("references public.alert_reservations (id) on delete cascade", migration)
        self.assertIn("check (position between 1 and 3)", migration)
        self.assertIn("primary key (reservation_id, position)", migration)
        self.assertIn("unique (reservation_id, latitude, longitude)", migration)

    def test_alert_places_moves_the_single_place_columns(self) -> None:
        migration = self._alert_places_migration()
        self.assertIn(
            "insert into public.alert_reservation_places (reservation_id, position, place_name, latitude, longitude)"
            " select id, 1, place_name, latitude, longitude from public.alert_reservations",
            migration,
        )
        for column in ("place_name", "latitude", "longitude"):
            self.assertIn(f"drop column {column}", migration)
        # 옛 함수(장소 칸 3개를 받던 것)는 지운다. 남겨 두면 지운 칸을 쓰다 실패한다.
        self.assertIn(
            "drop function public.create_alert_reservation(text, text, text, double precision,"
            " double precision, integer, text, text, timestamptz, integer, integer)",
            migration,
        )

    def test_alert_places_functions_are_service_role_only(self) -> None:
        migration = self._alert_places_migration()
        signatures = (
            "create_alert_reservation(text, text, jsonb, integer, text, text, timestamptz, integer, integer)",
            "alert_candidate_items(bigint, text)",
            "alert_message_payload(bigint)",
        )
        for signature in signatures:
            self.assertIn(
                f"revoke all on function public.{signature} from public, anon, authenticated", migration
            )
            self.assertIn(f"grant execute on function public.{signature} to service_role", migration)
        self.assertEqual(migration.count("security invoker"), len(signatures))
        self.assertEqual(migration.count("set search_path = ''"), len(signatures))

    def test_alert_places_cover_every_radius_and_name_the_nearest(self) -> None:
        migration = self._alert_places_migration()
        # 설계 16장 — 장소마다 반경 안 기관을 합친다(같은 기관은 한 번). PRD 4.1 — 구면 계산.
        self.assertIn("select distinct location.id", migration)
        self.assertIn("reservation.radius_m, false", migration)
        self.assertIn("join scope on scope.id = item.storage_location_id", migration)
        # 장소는 1~3곳만 받는다.
        self.assertIn("jsonb_array_length(p_places) not between 1 and 3", migration)
        # 메일 속 물품마다 가장 가까운 장소
        self.assertIn("'near_place_name'", migration)
        self.assertIn("order by extensions.st_distance(", migration)


    def _alert_review_migration(self) -> str:
        return " ".join(
            (MIGRATIONS / "20261115220000_alert_review_fixes.sql").read_text(encoding="utf-8").lower().split()
        )

    def test_alert_review_functions_are_service_role_only(self) -> None:
        migration = self._alert_review_migration()
        signatures = ("alert_candidate_items(bigint, text)", "discard_alert_reservation(bigint, text)")
        for signature in signatures:
            self.assertIn(
                f"revoke all on function public.{signature} from public, anon, authenticated", migration
            )
            self.assertIn(f"grant execute on function public.{signature} to service_role", migration)
        self.assertEqual(migration.count("security invoker"), len(signatures))
        self.assertEqual(migration.count("set search_path = ''"), len(signatures))

    def test_alert_cursor_follows_when_an_item_became_visible(self) -> None:
        # 리뷰 2026-11-15 — 나중에 기관이 연결된 물품도 알린다. 예약 이후 새 물품만이라는 규칙은 따로 지킨다.
        migration = self._alert_review_migration()
        self.assertIn(
            "alter table public.found_item_search_documents add column visible_since timestamptz not null default now()",
            migration,
        )
        self.assertIn("on document.visible_since > reservation.checked_until", migration)
        self.assertIn("and item.first_seen_at > reservation.created_at", migration)
        self.assertIn("(select max(candidates.visible_since) from candidates)", migration)
        # 절대 규칙 5 — 투영(matched 물품에만 있음)과 반경 범위를 그대로 거친다.
        self.assertIn("join scope on scope.id = document.storage_location_id", migration)

    def test_discarding_a_failed_reservation_frees_its_ip_slot_only(self) -> None:
        migration = self._alert_review_migration()
        self.assertIn("and status = 'pending' returning created_at into v_created_at", migration)
        self.assertIn("request.created_at = v_created_at limit 1", migration)

    def test_item_categories_cover_every_ingestion_parent_category(self) -> None:
        # PRD 2.2, 4.5 — 수집 코드의 상위분류 21개가 DB에 모두 있어야 기타물품 대체가 오분류를 만들지 않는다.
        from retriever_lost_found.ingestion.categories import SERVICE_CATEGORIES

        core = (MIGRATIONS / "20260826133434_create_lost_found_core_schema.sql").read_text(encoding="utf-8")
        expansion = (MIGRATIONS / "20261120090000_all_item_categories.sql").read_text(encoding="utf-8")
        for category in SERVICE_CATEGORIES:
            with self.subTest(code=category.code):
                pair = f"'{category.code}', '{category.name}'"
                self.assertTrue(pair in core or pair in expansion)
        lowered = expansion.lower()
        # 기존 6개 행·id와 물품은 건드리지 않고 새 분류만 더한다.
        self.assertIn("on conflict (code) do nothing", lowered)
        for statement in ("delete from", "update public.item_categories", "drop "):
            self.assertNotIn(statement, lowered)

    def test_keyword_candidates_drop_the_kind_mask_filter_and_stay_service_role_only(self) -> None:
        # PRD 4.1, 4.5 — 종류 판단은 서버 동의어 사전이 p_terms로 넘긴다. DB kind_mask는 별칭을 빠뜨린다.
        migration = " ".join(
            (MIGRATIONS / "20261120120000_keyword_candidates_without_kind_mask.sql")
            .read_text(encoding="utf-8").lower().split()
        )
        body = migration.split("create function public.keyword_found_item_candidates", 1)[1]
        self.assertIn("drop function public.keyword_found_item_candidates(bigint[],text[],text[]);", migration)
        self.assertIn("(p_location_ids bigint[],p_terms text[]) returns json", body)
        self.assertNotIn("kind_mask", body)
        self.assertNotIn("p_kinds", body)
        # 범위 필터와 단어 포함 조건은 그대로 남아야 한다.
        self.assertIn("d.storage_location_id=any($1) and cardinality($1) between 1 and 300", body)
        self.assertIn("strpos(d.search_text,lower(t.term))>0", body)
        self.assertIn("security invoker", body)
        signature = "public.keyword_found_item_candidates(bigint[],text[])"
        self.assertIn(f"revoke all on function {signature} from public,anon,authenticated", migration)
        self.assertIn(f"grant execute on function {signature} to service_role", migration)

    def test_keyword_candidates_compare_one_letter_words_as_standalone_words(self) -> None:
        # PRD 4.5 — 1글자 단어는 BM25처럼 독립 단어로만 통과시키고, 나머지 strpos 조건과 범위 필터는 그대로 둔다.
        migration = self._squashed("20261121120000_keyword_candidates_word_terms.sql")
        self.assertIn("drop function public.keyword_found_item_candidates(bigint[],text[]);", migration)
        body = migration.split("create function public.keyword_found_item_candidates", 1)[1]
        # 기본값이 있어야 2인자로 부르는 이전 앱도 동작한다.
        self.assertIn("(p_location_ids bigint[],p_terms text[],p_word_terms text[] default '{}') returns json", body)
        self.assertIn("d.storage_location_id=any($1) and cardinality($1) between 1 and 300", body)
        self.assertIn("strpos(d.search_text,lower(t.term))>0", body)
        self.assertIn("'(^|[^[:alnum:]])'||regexp_replace(lower(w.word),'([^[:alnum:]])','\\\\\\1','g')"
                      "||'($|[^[:alnum:]])'", body)
        self.assertIn("security invoker", body)
        self.assertNotIn("kind_mask", body)
        signature = "public.keyword_found_item_candidates(bigint[],text[],text[])"
        self.assertIn(f"revoke all on function {signature} from public,anon,authenticated", migration)
        self.assertIn(f"grant execute on function {signature} to service_role", migration)
        rollback = self._squashed("../rollbacks/20261121120000_keyword_candidates_word_terms.sql")
        self.assertIn(f"drop function if exists {signature};", rollback)
        self.assertIn("(p_location_ids bigint[],p_terms text[]) returns json", rollback)

    @staticmethod
    def _squashed(name: str) -> str:
        """SQL 주석(`--` 이후)을 빼고 공백을 정규화한다. 주석 속 설명이 검사에 걸리지 않게 한다."""
        lines = (MIGRATIONS / name).read_text(encoding="utf-8").lower().splitlines()
        return " ".join(" ".join(line.split("--", 1)[0] for line in lines).split())

    def test_embedding_cycle_checks_use_partial_indexes_instead_of_full_scans(self) -> None:
        # PRD 4.5, 5 — 62만 건에서 전체 힙 스캔 누락 확인이 8.9초로 Data API 8초 제한(57014)을 넘었다.
        migration = self._squashed("20261120143159_embedding_scan_indexes.sql")
        self.assertIn("on public.found_items (id) where embedding_input <> ''", migration)
        self.assertIn(
            "on public.found_item_embeddings_bge_m3 (item_id) "
            "where is_current and input_version = 'color_ko_item_name:v2'", migration)
        body = migration.split("create or replace function public.ensure_embedding_cycle", 1)[1]
        # 하루 단위 건수는 색인을 쓸 수 있는 범위 조건이어야 한다. 행마다 변환하는 식은 전체 스캔이다.
        self.assertNotIn("(first_seen_at at time zone 'asia/seoul')::date=p_business_date", body)
        self.assertIn("first_seen_at>=(p_business_date::timestamp at time zone 'asia/seoul')", body)
        self.assertIn("first_seen_at<((p_business_date+1)::timestamp at time zone 'asia/seoul')", body)
        signature = "public.ensure_embedding_cycle(date,text,boolean)"
        self.assertIn(f"revoke all on function {signature} from public,anon,authenticated", migration)
        self.assertIn(f"grant execute on function {signature} to service_role", migration)

    def test_backfill_runs_do_not_create_historical_embedding_cycles(self) -> None:
        # PRD 4.5 — 과거 날짜 회차가 다음 날 정규 회차를 뒤로 밀지 않게 한다. 정규 실행은 그대로 회차를 만든다.
        migration = self._squashed("20261120143345_skip_backfill_embedding_cycles.sql")
        body = migration.split("create or replace function public.enqueue_embeddings_on_ingestion_finish", 1)[1]
        skip = "if coalesce(new.metadata->>'trigger','')='manual_backfill' then return new; end if;"
        self.assertIn(skip, body)
        self.assertIn("perform public.ensure_embedding_run(", body)
        self.assertLess(body.index(skip), body.index("perform public.ensure_embedding_run("))

    def test_embedding_batches_pick_missing_ids_from_indexes_before_reading_rows(self) -> None:
        # PRD 4.5, 5 — after_id=0인 새 회차의 첫 조회가 8~10초였다. ID를 먼저 고르고 고른 행만 읽는다.
        migration = self._squashed("20261120143534_indexed_embedding_batches.sql")
        batch = migration.split("create or replace function public.embedding_run_batch", 1)[1].split(
            "create or replace function public.reuse_run_embeddings", 1)[0]
        reuse = migration.split("create or replace function public.reuse_run_embeddings", 1)[1]
        for body in (batch, reuse):
            self.assertIn("with ids as materialized ( select f.id from public.found_items f", body)
            self.assertIn("raise exception 'embedding lease unavailable'", body)
        self.assertIn("raise exception 'ingestion still running'", batch)
        self.assertIn("perform public.reuse_run_embeddings(p_run_id,p_token,p_after);", batch)
        self.assertIn("e.input_version='color_ko_item_name:v2' and e.is_current", batch)
        self.assertIn("limit least(greatest(p_limit,1),64)", batch)
        self.assertIn("for share of f skip locked", reuse)
        self.assertIn("on conflict(item_id) do nothing", reuse)
        for signature in ("public.embedding_run_batch(bigint,uuid,bigint,integer)",
                          "public.reuse_run_embeddings(bigint,uuid,bigint,integer)"):
            self.assertIn(f"revoke all on function {signature} from public,anon,authenticated", migration)
            self.assertIn(f"grant execute on function {signature} to service_role", migration)

    def test_unused_search_objects_are_dropped_but_live_search_and_kind_column_stay(self) -> None:
        # PRD 5 — 용량 정리는 앱이 부르지 않는 객체만 지운다. 살아 있는 검색 RPC와 kind_mask 생성 열의 함수는 남긴다.
        migration = self._squashed("20261121000000_drop_unused_search_objects.sql")
        for dropped in (
            "drop index if exists public.found_item_search_documents_kind;",
            "drop index if exists public.search_engine_matched_location_id_idx;",
            "drop function if exists public.search_found_items_semantic_fast(",
            "drop function if exists public.search_found_item_candidates(",
            "drop function if exists public.rerank_found_item_candidates(",
            "drop function if exists public.read_found_item_vector_scores(",
            "drop function if exists public.search_found_items_semantic(",
            "drop function if exists public.allowed_search_kind_masks(",
        ):
            self.assertIn(dropped, migration)
        for kept in ("keyword_found_item_candidates", "keyword_rerank_vectors", "search_found_item_details",
                     "public.search_kind_mask(", "found_item_search_documents_location", "drop table", "drop column"):
            self.assertNotIn(kept, migration)
        # 앱 코드가 지운 RPC를 부르지 않아야 한다.
        source = "\n".join(path.read_text(encoding="utf-8") for path in
                           (MIGRATIONS.parents[1] / "src").rglob("*.py"))
        for name in ("search_found_items_semantic", "search_found_item_candidates",
                     "rerank_found_item_candidates", "read_found_item_vector_scores", "allowed_search_kind_masks"):
            self.assertNotIn(name, source)
        rollback = self._squashed("../rollbacks/20261121000000_drop_unused_search_objects.sql")
        self.assertIn("create index if not exists found_item_search_documents_kind", rollback)
        self.assertIn("create or replace function public.allowed_search_kind_masks(p_kinds text[])", rollback)

    def test_one_bit_semantic_search_replaces_the_full_vector_copy(self) -> None:
        # PRD 4.1, 4.5, 5 — 2026-11-21 확정안: 1비트 해밍 후보 500건 → 원본 halfvec 재채점.
        # 원본 사본·2비트·실험 함수는 지우고, 동기화는 1비트 표만 유지한다.
        [path] = MIGRATIONS.glob("*_one_bit_semantic_search.sql")
        # GitHub 연동은 운영 최신 버전보다 뒤 번호만 순서대로 적용한다.
        self.assertGreater(path.name.split("_", 1)[0], "20261121180000")
        migration = self._squashed(path.name)
        self.assertIn("alter table public.found_item_embeddings_bge_m3 alter column embedding set storage plain;",
                      migration)
        for dropped in ("drop table public.found_item_search_vectors;",
                        "drop table if exists public.found_item_search_vectors_2bit;",
                        "drop function if exists public.exp_semantic_1bit("):
            self.assertIn(dropped, migration)
        self.assertNotIn("cascade;", migration.replace("on delete cascade;", ""))
        refresh = migration.split("create or replace function public.refresh_search_vector", 1)[1].split(
            "create or replace function public.keyword_rerank_vectors", 1)[0]
        self.assertIn("insert into public.found_item_search_vectors_1bit", refresh)
        self.assertIn("extensions.binary_quantize(e.embedding)::bit(1024)", refresh)
        self.assertIn("delete from public.found_item_search_vectors_1bit where item_id = p_item_id;", refresh)
        semantic = migration.split("create function public.semantic_found_item_candidates", 1)[1]
        self.assertIn("order by bit_count(b.embedding # q), b.item_id desc limit p_rescore", semantic)
        self.assertIn("join public.found_item_embeddings_bge_m3 e on e.item_id = c.item_id", semantic)
        self.assertIn("p_min float8 default 0.6, p_limit int default 200, p_rescore int default 500", semantic)
        self.assertIn("security invoker", semantic)
        signature = "public.semantic_found_item_candidates(bigint[], extensions.halfvec, float8, int, int)"
        self.assertIn(f"revoke all on function {signature} from public, anon, authenticated", migration)
        self.assertIn(f"grant execute on function {signature} to service_role", migration)
        rollback = self._squashed(f"../rollbacks/{path.name}")
        self.assertIn("create table public.found_item_search_vectors (", rollback)
        self.assertIn(f"drop function if exists {signature};", rollback)


    def test_embedding_rows_stay_inline_and_unused_rerank_rpc_is_dropped(self) -> None:
        # PRD 4.5, 5 — 벡터를 plain으로 두면 행이 약 2.26KB라 짧은 글자 열이 TOAST로 밀린다. 4000 이하 행은 그대로 둔다.
        [path] = MIGRATIONS.glob("*_inline_embedding_rows.sql")
        self.assertGreater(path.name.split("_", 1)[0], "20261121190000")
        migration = self._squashed(path.name)
        self.assertIn("alter table public.found_item_embeddings_bge_m3 set (toast_tuple_target = 4000);", migration)
        self.assertIn("drop function if exists public.keyword_rerank_vectors(bigint[], bigint[]);", migration)
        self.assertNotIn("drop table", migration)
        source = "\n".join(p.read_text(encoding="utf-8") for p in (MIGRATIONS.parents[1] / "src").rglob("*.py"))
        self.assertNotIn("keyword_rerank_vectors", source)
        rollback = self._squashed(f"../rollbacks/{path.name}")
        self.assertIn("reset (toast_tuple_target)", rollback)
        self.assertIn("create or replace function public.keyword_rerank_vectors", rollback)

    def test_gemini2_embeddings_add_storage_without_touching_the_live_bge_path(self) -> None:
        # PRD 4.5, 5 — Gemini 벡터 저장소만 추가한다. 앱 검색·크론 임베딩·BGE 동기화 함수는 그대로다.
        [path] = MIGRATIONS.glob("*_gemini2_embeddings.sql")
        self.assertGreater(path.name.split("_", 1)[0], "20261121211000")
        migration = self._squashed(path.name)
        for expected in (
            "create table public.found_item_embeddings_gemini2 (",
            "embedding extensions.halfvec(1024) not null",
            ") with (toast_tuple_target = 4000);",
            "alter table public.found_item_embeddings_gemini2 alter column embedding set storage plain;",
            "create table public.found_item_search_vectors_gemini2_1bit (",
            "references public.found_item_embeddings_gemini2(item_id) on delete cascade",
            "create function public.upsert_gemini2_embeddings(records jsonb)",
            "jsonb_array_length(records) not between 1 and 200",
            "and (s.image_url is null or s.image_url = f.image_url)",
            "p_min float8 default 0.525, p_limit int default 200, p_rescore int default 500)",
            "after update of storage_location_id, location_match_status on public.found_items",
        ):
            self.assertIn(expected, migration)
        for untouched in ("refresh_search_vector", "sync_search_document", "found_item_embeddings_bge_m3",
                          "semantic_found_item_candidates("):
            self.assertNotIn(untouched, migration)
        self.assertNotIn("drop table", migration)
        for table in ("found_item_embeddings_gemini2", "found_item_search_vectors_gemini2_1bit"):
            self.assertIn(f"alter table public.{table} enable row level security;", migration)
        self.assertIn("from public, anon, authenticated;", migration)
        source = "\n".join(p.read_text(encoding="utf-8") for p in (MIGRATIONS.parents[1] / "src").rglob("*.py"))
        self.assertNotIn("gemini2", source)  # 검색 전환 전까지 앱은 Gemini 표를 읽지 않는다.
        rollback = self._squashed(f"../rollbacks/{path.name}")
        self.assertIn("drop trigger if exists found_items_sync_gemini2_vector on public.found_items;", rollback)
        self.assertIn("drop table if exists public.found_item_embeddings_gemini2;", rollback)

if __name__ == "__main__":
    unittest.main()
