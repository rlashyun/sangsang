# ③ 스케줄러(분실물 알림 예약) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사용자가 검색 조건과 이메일로 알림을 예약하면, 예약 기간 동안 매일 새로 들어온 습득물 중 유사한 물품을 이메일로 알린다(PRD 2.1-4, 4.2 P1).

**Architecture:** Supabase에 예약·대기열 테이블 5개와 DB 함수 12개를 두고, 서버는 `src/retriever_lost_found/alerts/` 패키지로 예약(API)·매칭(수집 크론 끝)·발송(새 발송 크론)을 나눈다. 메일은 표준 라이브러리 `smtplib`로 Gmail SMTP에 보낸다. 유사 판정은 ⑥ 결론 전까지 비워 두고(`SIMILARITY_JUDGE = None`), 경계 함수 `find_new_similar_items`로 나중에 끼운다.

**Tech Stack:** Python 3.12 · FastAPI · Supabase(PostgREST RPC, PostGIS) · `smtplib`·`hmac`·`urllib`(새 의존성 없음) · `unittest` + pytest

**Spec:** `docs/alert-scheduler/2026-11-06-email-scheduler-design.md` (이 계획이 정한 세부는 그 문서 14장)

## Global Constraints

- 브랜치는 `feat-alert-scheduler`. `main`에 직접 커밋하지 않는다. push는 사용자가 요청할 때만 한다.
- 커밋 메시지는 `{type}: {요약} — {PRD ID}` 형식이고 끝에 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`를 붙인다.
- 새 의존성을 추가하지 않는다(`smtplib`·`hmac`·`hashlib`·`urllib`·`email`만 쓴다).
- 구현 근거를 PRD 절·설계 절 번호로 주석에 남긴다(`AGENTS.md` 6장).
- 오류 응답은 한국어. 400 입력 오류(`ValueError`) · 429 제한 초과 · 502 외부 장애(`RuntimeError`) · 503 설정 없음. 크론은 401·500도 쓴다.
- 테스트는 외부 서비스를 부르지 않는다(가짜 발송기·가짜 저장소). 막히는 경우는 되는 경우와 짝으로 확인한다. 테스트가 실패하면 구현을 고친다.
- 테스트 파일 이름은 저장소 전체에서 겹치지 않게 `test_alert_*.py`로 짓는다(`tests/`에 `__init__.py`가 없다).
- 비밀값(`MAIL_SMTP_PASSWORD`, `ALERT_SECRET`, `SUPABASE_SECRET_KEY`, `CRON_SECRET`)은 서버 전용. 환경 변수 이름은 `VITE_`로 시작하지 않는다(절대 규칙 4).
- 모든 새 테이블은 RLS를 켜고 `anon`·`authenticated` 권한을 회수하고 `service_role`에만 준다. DB 함수는 `security invoker`, `set search_path = ''`, `service_role`에만 `execute`(절대 규칙 6).
- 메일 문구는 "유사한 물품이 새로 등록됐습니다"로 쓰고 본인 물건이라고 단정하지 않는다(`AGENTS.md` 2장 10번 부연).
- `src/retriever_lost_found/ingestion/service.py`는 고치지 않는다. `/api/cron/partner-sync`·`police-sync`의 응답 본문과 상태 코드는 바꾸지 않는다.
- 화면 파일(`frontend/` 전체)은 고치지 않는다 — 강지현 담당(사용자 지시).
- 고정값: 하루 발송 상한 450통(최근 24시간) · 같은 이메일 진행 중 예약 3개 · 같은 IP 24시간 5건 · 확인 링크 24시간 · 만료 = 등록일(KST) + 7일의 11:00 KST · 연장 +7일 최대 3회 · 만료 안내 = 만료까지 36시간 이하 · 시간 예산 260초 · 메일 한 통 물품 최대 5개 · `sending` 방치 10분 · 일시 오류 3번째 시도에서 실패 · 발송 크론 `"0 2 * * *"`.
- 테스트 명령: `uv run --with 'pytest>=8,<9' pytest -q`. OneDrive 폴더에서 `uv`가 `액세스가 거부되었습니다 (os error 5)`로 실패하면 `uv run --no-sync --with 'pytest>=8,<9' pytest -q`로 실행한다.

---

## 파일 지도

| 파일 | 할 일 | 책임 |
| --- | --- | --- |
| `supabase/migrations/20261106120000_create_alert_scheduler.sql` | 새로 | 테이블 5개, 인덱스, DB 함수 12개, 권한 |
| `tests/database/verify_alert_scheduler.sql` | 새로 | DB 함수 수동 검증(rollback) |
| `src/retriever_lost_found/alerts/__init__.py` | 새로 | 패키지 표시 |
| `src/retriever_lost_found/alerts/errors.py` | 새로 | `AlertLimitError`, `safe_error` |
| `src/retriever_lost_found/alerts/settings.py` | 새로 | 환경 변수 → `AlertSettings` |
| `src/retriever_lost_found/alerts/links.py` | 새로 | 링크 토큰, IP 해시, 만료 시각, 메일 링크 |
| `src/retriever_lost_found/alerts/sender.py` | 새로 | `OutgoingEmail`, SMTP 발송, 오류 분류 |
| `src/retriever_lost_found/alerts/templates.py` | 새로 | 메일 3종 본문 |
| `src/retriever_lost_found/alerts/store.py` | 새로 | `AlertStore` 규약, `SupabaseAlertStore` |
| `src/retriever_lost_found/alerts/reservations.py` | 새로 | `AlertService`(생성·요약·확인·연장·취소) |
| `src/retriever_lost_found/alerts/matching.py` | 새로 | 판정 경계, 매칭 단계 |
| `src/retriever_lost_found/alerts/dispatch.py` | 새로 | 발송 단계 |
| `src/retriever_lost_found/alerts/send_test.py` | 새로 | 발송 테스트 명령 |
| `src/retriever_lost_found/web/alerts.py` | 새로 | `/api/alerts*` 라우터 |
| `src/retriever_lost_found/web/app.py` | 수정 | 알림 라우터 연결 |
| `src/retriever_lost_found/web/cron.py` | 수정 | 매칭 연결, `/api/cron/alert-send` |
| `vercel.json` | 수정 | 발송 크론 추가 |
| `tests/alerts/alert_fakes.py` | 새로 | 테스트용 설정·가짜 저장소·가짜 발송기 |
| `tests/alerts/test_alert_*.py` | 새로 | 알림 로직 테스트 |
| `tests/web/test_alert_api.py`, `tests/web/test_alert_cron.py` | 새로 | API·크론 테스트 |
| `tests/database/test_schema_migrations.py` | 수정 | 새 마이그레이션 SQL 검사 추가 |
| `tests/deployment/test_vercel_config.py` | 수정 | 크론 기대값 2개 → 3개(11-06 팀 결정) |
| `tests/deployment/test_alert_env.py` | 새로 | `.env.example`·CI에 새 변수 |
| `.env.example`, `README.md`, `.github/workflows/ci.yml` | 수정 | 새 환경 변수 |
| `docs/PRD.md`, `AGENTS.md`, `docs/team-roles.md` | 수정 | 크론 3개, 엔드포인트, 결정 사항 |

작업 순서와 선행 관계: 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10. 11은 ⑥ 결론 뒤, 12는 사용자 확인이 필요한 운영 작업이다.

---
### Task 1: DB 스키마 — 테이블·인덱스·DB 함수·권한

**Files:**
- Create: `supabase/migrations/20261106120000_create_alert_scheduler.sql`
- Create: `tests/database/verify_alert_scheduler.sql`
- Modify: `tests/database/test_schema_migrations.py` (클래스 끝에 테스트 3개 추가)

**Interfaces:**
- Produces (DB 함수, 모두 `service_role` 전용):
  - `create_alert_reservation(p_email text, p_query text, p_place_name text, p_latitude double precision, p_longitude double precision, p_radius_m integer, p_ip_hash text, p_link_nonce text, p_expires_at timestamptz, p_email_limit integer default 3, p_ip_daily_limit integer default 5) → jsonb` `{"status": "created"|"email_limit"|"ip_limit", "reservation_id", "message_id", "expires_at"}`
  - `confirm_alert_reservation(p_reservation_id bigint) → text` `confirmed|already_active|link_expired|ended|not_found`
  - `extend_alert_reservation(p_reservation_id bigint, p_max_extensions integer default 3) → jsonb` `{"result": "extended"|"limit_reached"|"ended"|"not_found", "expires_at", "extensions_left"}`
  - `cancel_alert_reservation(p_reservation_id bigint) → text` `cancelled|already_ended|not_found`
  - `alert_match_queue(p_item_source_code text) → table(id, query, checked_until)`
  - `alert_candidate_items(p_reservation_id bigint, p_item_source_code text) → jsonb` `{"checked_until", "items": [...]}`
  - `record_alert_match(p_reservation_id bigint, p_item_source_code text, p_checked_until timestamptz, p_items jsonb) → jsonb` `{"items_queued", "message_id"}`
  - `enqueue_alert_expiry_notices(p_window interval default '36 hours') → integer`
  - `claim_alert_messages(p_limit integer, p_daily_cap integer, p_message_id bigint default null) → setof alert_messages`
  - `alert_message_payload(p_message_id bigint) → jsonb` `{"message", "reservation", "items"}`
  - `finish_alert_message(p_message_id bigint, p_status text, p_error text default null) → void`
  - `close_ended_alert_reservations() → jsonb` `{"expired", "unconfirmed_closed", "messages_dropped"}`

- [ ] **Step 1: 실패하는 SQL 검사 테스트 쓰기**

`tests/database/test_schema_migrations.py`의 `SchemaMigrationTests` 클래스 끝(`if __name__ == "__main__":` 위)에 추가한다.

```python
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
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `uv run --with 'pytest>=8,<9' pytest tests/database/test_schema_migrations.py -q`
Expected: 새 테스트 3개가 `FileNotFoundError`로 FAIL, 기존 테스트는 PASS

- [ ] **Step 3: 마이그레이션 앞부분 — 테이블·인덱스 쓰기**

`supabase/migrations/20261106120000_create_alert_scheduler.sql`을 만든다.

```sql
-- ③ 스케줄러(분실물 알림 예약) — PRD 2.1-4, 4.2 P1.
-- 설계: docs/alert-scheduler/2026-11-06-email-scheduler-design.md 4장.
-- 저장한 검색 조건으로 공공 습득물 DB를 다시 탐색해 이메일로 알린다(AGENTS.md 2장 10번 부연).
-- 절대 규칙 6 — 모든 테이블은 RLS를 켜고 서버 키(service_role)만 접근한다.

-- 매칭 후보 조회가 "이 출처에서 커서 이후 처음 들어온 물품"을 바로 찾게 한다.
-- 없으면 예약마다 반경 안 물품 전체(서울역 5km 4만 건)를 훑는다.
create index if not exists found_items_source_first_seen_idx
    on public.found_items (item_source_code, first_seen_at);

create table public.alert_reservations (
    id bigint generated always as identity primary key,
    email text,
    query text not null,
    place_name text not null,
    latitude double precision not null,
    longitude double precision not null,
    radius_m integer not null,
    status text not null default 'pending',
    link_nonce text not null,
    ip_hash text,
    consent_at timestamptz not null,
    created_at timestamptz not null default now(),
    confirmed_at timestamptz,
    expires_at timestamptz not null,
    extension_count smallint not null default 0,
    ended_at timestamptz,
    constraint alert_reservations_query_length
        check (char_length(btrim(query)) between 1 and 30),
    constraint alert_reservations_place_not_blank check (btrim(place_name) <> ''),
    constraint alert_reservations_position_valid
        check (latitude between -90 and 90 and longitude between -180 and 180),
    constraint alert_reservations_radius_allowed check (radius_m in (1000, 3000, 5000)),
    constraint alert_reservations_status_valid
        check (status in ('pending', 'active', 'expired', 'cancelled', 'unconfirmed_expired')),
    constraint alert_reservations_extension_range check (extension_count between 0 and 3),
    constraint alert_reservations_nonce_not_blank check (btrim(link_nonce) <> ''),
    -- 설계 8장 — 끝난 예약에는 이메일·IP 해시가 남을 수 없다.
    constraint alert_reservations_personal_data_lifecycle check (
        (status in ('pending', 'active') and email is not null)
        or (status in ('expired', 'cancelled', 'unconfirmed_expired') and email is null and ip_hash is null)
    )
);

create index alert_reservations_status_expires_idx
    on public.alert_reservations (status, expires_at);
create index alert_reservations_open_email_idx
    on public.alert_reservations (email)
    where status in ('pending', 'active');
create index alert_reservations_ip_created_idx
    on public.alert_reservations (ip_hash, created_at)
    where ip_hash is not null;

-- 설계 4.2 — 출처별로 어디까지 확인했는지. 판정에 걸리지 않은 물품이 매일 다시 후보가 되지 않게 한다.
create table public.alert_match_cursors (
    reservation_id bigint not null
        references public.alert_reservations(id) on delete cascade,
    item_source_code text not null
        references public.item_sources(code) on update cascade on delete restrict,
    checked_until timestamptz not null,
    primary key (reservation_id, item_source_code)
);

-- 설계 4.3 — 보낼 메일 대기열. 450통 한도는 여기서 최근 24시간 sent를 센다.
create table public.alert_messages (
    id bigint generated always as identity primary key,
    reservation_id bigint not null
        references public.alert_reservations(id) on delete cascade,
    kind text not null,
    status text not null default 'pending',
    expiry_for timestamptz,
    attempts smallint not null default 0,
    claimed_at timestamptz,
    sent_at timestamptz,
    last_error text,
    created_at timestamptz not null default now(),
    constraint alert_messages_kind_valid check (kind in ('confirm', 'match', 'expiry_notice')),
    constraint alert_messages_status_valid check (status in ('pending', 'sending', 'sent', 'failed')),
    constraint alert_messages_expiry_for_matches_kind
        check ((kind = 'expiry_notice') = (expiry_for is not null)),
    constraint alert_messages_sent_at_matches_status
        check ((status = 'sent') = (sent_at is not null))
);

create unique index alert_messages_one_pending_match_per_reservation
    on public.alert_messages (reservation_id)
    where kind = 'match' and status = 'pending';
create unique index alert_messages_one_expiry_notice_per_expiry
    on public.alert_messages (reservation_id, expiry_for)
    where kind = 'expiry_notice';
create unique index alert_messages_one_confirm_per_reservation
    on public.alert_messages (reservation_id)
    where kind = 'confirm';
create index alert_messages_status_kind_created_idx
    on public.alert_messages (status, kind, created_at);
create index alert_messages_sent_at_idx
    on public.alert_messages (sent_at)
    where status = 'sent';

-- 설계 4.4 — 알린 물품. 한 번 알린 물품은 다시 들어갈 수 없다.
create table public.alert_notified_items (
    reservation_id bigint not null
        references public.alert_reservations(id) on delete cascade,
    found_item_id bigint not null
        references public.found_items(id) on delete cascade,
    message_id bigint not null
        references public.alert_messages(id) on delete cascade,
    score real,
    created_at timestamptz not null default now(),
    primary key (reservation_id, found_item_id)
);

create index alert_notified_items_message_idx
    on public.alert_notified_items (message_id);
-- found_items 보존기간 삭제(5,000건 배치)가 cascade로 이 표를 찾을 때 전체를 훑지 않게 한다.
create index alert_notified_items_found_item_idx
    on public.alert_notified_items (found_item_id);

-- 설계 4.5 — 감사 로그 불변식. 매칭(출처별)과 발송 실행을 모두 남긴다.
create table public.alert_runs (
    id bigint generated always as identity primary key,
    kind text not null,
    item_source_code text
        references public.item_sources(code) on update cascade on delete restrict,
    started_at timestamptz not null default now(),
    finished_at timestamptz,
    status text not null default 'running',
    reservations_checked integer not null default 0,
    reservations_matched integer not null default 0,
    items_queued integer not null default 0,
    expiry_notices_queued integer not null default 0,
    messages_sent integer not null default 0,
    messages_deferred integer not null default 0,
    messages_failed integer not null default 0,
    reservations_expired integer not null default 0,
    reservations_unconfirmed_closed integer not null default 0,
    error_message text,
    metadata jsonb not null default '{}'::jsonb,
    constraint alert_runs_kind_valid check (kind in ('match', 'send')),
    constraint alert_runs_status_valid check (status in ('running', 'succeeded', 'partial', 'failed')),
    constraint alert_runs_source_matches_kind check ((kind = 'match') = (item_source_code is not null)),
    constraint alert_runs_finished_state_valid check ((status = 'running') = (finished_at is null))
);

create index alert_runs_kind_started_idx
    on public.alert_runs (kind, started_at desc);
```

- [ ] **Step 4: 마이그레이션 이어서 — 예약 DB 함수 4개**

같은 파일 끝에 이어 붙인다.

```sql
-- 설계 5.4 — 예약 생성. 이메일 3개·IP 24시간 5건 제한을 확인하고 예약·커서·확인 메일 행을 한 번에 만든다.
create or replace function public.create_alert_reservation(
    p_email text,
    p_query text,
    p_place_name text,
    p_latitude double precision,
    p_longitude double precision,
    p_radius_m integer,
    p_ip_hash text,
    p_link_nonce text,
    p_expires_at timestamptz,
    p_email_limit integer default 3,
    p_ip_daily_limit integer default 5
)
returns jsonb
language plpgsql
volatile
security invoker
set search_path = ''
as $$
declare
    v_reservation_id bigint;
    v_message_id bigint;
begin
    -- 같은 이메일·IP의 동시 요청이 제한을 함께 통과하지 못하게 예약 생성을 한 줄로 세운다.
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('alert_reservation_create'));

    if (
        select count(*)
        from public.alert_reservations as reservation
        where reservation.email = p_email
          and reservation.status in ('pending', 'active')
    ) >= p_email_limit then
        return jsonb_build_object('status', 'email_limit');
    end if;

    if p_ip_hash is not null and (
        select count(*)
        from public.alert_reservations as reservation
        where reservation.ip_hash = p_ip_hash
          and reservation.created_at > now() - interval '24 hours'
    ) >= p_ip_daily_limit then
        return jsonb_build_object('status', 'ip_limit');
    end if;

    insert into public.alert_reservations (
        email, query, place_name, latitude, longitude, radius_m,
        status, link_nonce, ip_hash, consent_at, expires_at
    )
    values (
        p_email, btrim(p_query), btrim(p_place_name), p_latitude, p_longitude, p_radius_m,
        'pending', p_link_nonce, p_ip_hash, now(), p_expires_at
    )
    returning id into v_reservation_id;

    -- 설계 4.2 — 출처별 커서의 처음 값은 예약 생성 시각이다(예약 이후 새 물품만 비교).
    insert into public.alert_match_cursors (reservation_id, item_source_code, checked_until)
    select v_reservation_id, source.code, now()
    from public.item_sources as source;

    insert into public.alert_messages (reservation_id, kind)
    values (v_reservation_id, 'confirm')
    returning id into v_message_id;

    return jsonb_build_object(
        'status', 'created',
        'reservation_id', v_reservation_id,
        'message_id', v_message_id,
        'expires_at', p_expires_at
    );
end;
$$;

-- 설계 5.4 — 확인 링크는 만든 지 24시간 안에만 예약을 켠다.
create or replace function public.confirm_alert_reservation(p_reservation_id bigint)
returns text
language plpgsql
volatile
security invoker
set search_path = ''
as $$
declare
    v_status text;
    v_created_at timestamptz;
begin
    select reservation.status, reservation.created_at
    into v_status, v_created_at
    from public.alert_reservations as reservation
    where reservation.id = p_reservation_id
    for update;

    if not found then
        return 'not_found';
    end if;
    if v_status = 'active' then
        return 'already_active';
    end if;
    if v_status <> 'pending' then
        return 'ended';
    end if;
    if v_created_at <= now() - interval '24 hours' then
        return 'link_expired';
    end if;

    update public.alert_reservations
    set status = 'active', confirmed_at = now()
    where id = p_reservation_id;
    return 'confirmed';
end;
$$;

-- 설계 2장 — 연장은 활성이고 만료 전인 예약만, 한 번에 +7일, 최대 3회.
create or replace function public.extend_alert_reservation(
    p_reservation_id bigint,
    p_max_extensions integer default 3
)
returns jsonb
language plpgsql
volatile
security invoker
set search_path = ''
as $$
declare
    v_status text;
    v_expires_at timestamptz;
    v_count smallint;
begin
    select reservation.status, reservation.expires_at, reservation.extension_count
    into v_status, v_expires_at, v_count
    from public.alert_reservations as reservation
    where reservation.id = p_reservation_id
    for update;

    if not found then
        return jsonb_build_object('result', 'not_found');
    end if;
    if v_status <> 'active' or v_expires_at <= now() then
        return jsonb_build_object('result', 'ended');
    end if;
    if v_count >= p_max_extensions then
        return jsonb_build_object('result', 'limit_reached');
    end if;

    update public.alert_reservations
    set expires_at = expires_at + interval '7 days',
        extension_count = extension_count + 1
    where id = p_reservation_id
    returning expires_at, extension_count into v_expires_at, v_count;

    return jsonb_build_object(
        'result', 'extended',
        'expires_at', v_expires_at,
        'extensions_left', p_max_extensions - v_count
    );
end;
$$;

-- 설계 5.4 — 취소하면 이메일·IP 해시를 즉시 지우고 아직 안 보낸 메일도 지운다.
create or replace function public.cancel_alert_reservation(p_reservation_id bigint)
returns text
language plpgsql
volatile
security invoker
set search_path = ''
as $$
declare
    v_status text;
begin
    select reservation.status
    into v_status
    from public.alert_reservations as reservation
    where reservation.id = p_reservation_id
    for update;

    if not found then
        return 'not_found';
    end if;
    if v_status not in ('pending', 'active') then
        return 'already_ended';
    end if;

    update public.alert_reservations
    set status = 'cancelled', email = null, ip_hash = null, ended_at = now()
    where id = p_reservation_id;
    delete from public.alert_messages
    where reservation_id = p_reservation_id
      and status = 'pending';
    return 'cancelled';
end;
$$;
```

- [ ] **Step 5: 마이그레이션 이어서 — 매칭 DB 함수 3개**

같은 파일 끝에 이어 붙인다.

```sql
-- 설계 6.1 — 매칭할 활성 예약. 커서가 오래된 예약부터 처리해 시간 예산으로 멈춘 날 밀린 예약이 먼저 돈다.
create or replace function public.alert_match_queue(p_item_source_code text)
returns table (id bigint, query text, checked_until timestamptz)
language sql
stable
security invoker
set search_path = ''
as $$
    select reservation.id, reservation.query, match_cursor.checked_until
    from public.alert_reservations as reservation
    join public.alert_match_cursors as match_cursor
      on match_cursor.reservation_id = reservation.id
     and match_cursor.item_source_code = p_item_source_code
    where reservation.status = 'active'
      and reservation.expires_at > now()
    order by match_cursor.checked_until, reservation.id
$$;

-- 설계 4.6 — 후보 물품: 반경 안 활성 기관 · 이 출처 · 커서 이후 처음 들어옴 · 아직 안 알림.
-- 공간이 1차 필터다(PRD 4.1). 기관 목록은 저장하지 않고 매번 다시 계산한다.
create or replace function public.alert_candidate_items(
    p_reservation_id bigint,
    p_item_source_code text
)
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
    with reservation as (
        select r.id, r.latitude, r.longitude, r.radius_m, match_cursor.checked_until
        from public.alert_reservations as r
        join public.alert_match_cursors as match_cursor
          on match_cursor.reservation_id = r.id
         and match_cursor.item_source_code = p_item_source_code
        where r.id = p_reservation_id
          and r.status = 'active'
          and r.expires_at > now()
    ),
    scope as (
        -- 화면의 Haversine과 경계를 맞추려고 구면 계산(마지막 인자 false)을 쓴다.
        select location.id
        from reservation
        join public.storage_locations as location
          on location.is_active
         and extensions.st_dwithin(
             location.position,
             extensions.st_setsrid(
                 extensions.st_makepoint(reservation.longitude, reservation.latitude), 4326
             )::extensions.geography,
             reservation.radius_m, false
         )
    ),
    candidates as (
        select item.id, item.item_name, item.raw_category_name, item.color_name,
               item.description, item.normalized_search_text, item.registered_on,
               item.first_seen_at
        from reservation
        join public.found_items as item
          on item.item_source_code = p_item_source_code
         and item.first_seen_at > reservation.checked_until
        join scope on scope.id = item.storage_location_id
        where not exists (
            select 1
            from public.alert_notified_items as notified
            where notified.reservation_id = reservation.id
              and notified.found_item_id = item.id
        )
    )
    select jsonb_build_object(
        'checked_until',
        coalesce(
            (select max(candidates.first_seen_at) from candidates),
            (select reservation.checked_until from reservation)
        ),
        'items',
        coalesce(
            (
                select jsonb_agg(to_jsonb(candidates) order by candidates.first_seen_at, candidates.id)
                from candidates
            ),
            '[]'::jsonb
        )
    )
$$;

-- 설계 4.3·4.4 — 알린 물품 기록 + 대기 알림 메일에 붙이기 + 커서 이동을 한 번에 한다.
create or replace function public.record_alert_match(
    p_reservation_id bigint,
    p_item_source_code text,
    p_checked_until timestamptz,
    p_items jsonb
)
returns jsonb
language plpgsql
volatile
security invoker
set search_path = ''
as $$
declare
    v_message_id bigint;
    v_fresh_count integer;
    v_queued integer := 0;
begin
    -- 이미 알린 물품은 빼고 센다. 새 물품이 없으면 빈 알림 메일을 만들지 않는다.
    select count(*)
    into v_fresh_count
    from jsonb_array_elements(coalesce(p_items, '[]'::jsonb)) as entry
    where not exists (
        select 1
        from public.alert_notified_items as notified
        where notified.reservation_id = p_reservation_id
          and notified.found_item_id = (entry ->> 'found_item_id')::bigint
    );

    if v_fresh_count > 0 then
        -- 예약마다 대기 알림 메일은 1통. partner·police가 같은 메일에 물품을 더한다.
        insert into public.alert_messages (reservation_id, kind)
        values (p_reservation_id, 'match')
        on conflict (reservation_id) where kind = 'match' and status = 'pending' do nothing;

        select message.id
        into v_message_id
        from public.alert_messages as message
        where message.reservation_id = p_reservation_id
          and message.kind = 'match'
          and message.status = 'pending';

        insert into public.alert_notified_items (reservation_id, found_item_id, message_id, score)
        select p_reservation_id,
               (entry ->> 'found_item_id')::bigint,
               v_message_id,
               (entry ->> 'score')::real
        from jsonb_array_elements(p_items) as entry
        on conflict (reservation_id, found_item_id) do nothing;
        get diagnostics v_queued = row_count;
    end if;

    -- 판정에 걸리지 않은 물품도 다시 보지 않게 커서는 늘 움직인다(NULL이면 그대로).
    update public.alert_match_cursors
    set checked_until = greatest(checked_until, p_checked_until)
    where reservation_id = p_reservation_id
      and item_source_code = p_item_source_code;

    return jsonb_build_object('items_queued', v_queued, 'message_id', v_message_id);
end;
$$;
```

- [ ] **Step 6: 마이그레이션 이어서 — 발송 DB 함수 5개와 권한**

같은 파일 끝에 이어 붙인다.

```sql
-- 설계 5.3 — 만료 시각이 11:00 KST로 고정이라 만료 전날 발송 실행에서만 36시간 안에 든다.
create or replace function public.enqueue_alert_expiry_notices(
    p_window interval default interval '36 hours'
)
returns integer
language plpgsql
volatile
security invoker
set search_path = ''
as $$
declare
    v_count integer;
begin
    insert into public.alert_messages (reservation_id, kind, expiry_for)
    select reservation.id, 'expiry_notice', reservation.expires_at
    from public.alert_reservations as reservation
    where reservation.status = 'active'
      and reservation.expires_at > now()
      and reservation.expires_at <= now() + p_window
    on conflict (reservation_id, expiry_for) where kind = 'expiry_notice' do nothing;
    get diagnostics v_count = row_count;
    return v_count;
end;
$$;

-- 설계 4.6 — 450통 한도 안에서 보낼 메일을 집는다. 동시에 불려도 한도를 넘지 않게 잠근다.
create or replace function public.claim_alert_messages(
    p_limit integer,
    p_daily_cap integer,
    p_message_id bigint default null
)
returns setof public.alert_messages
language plpgsql
volatile
security invoker
set search_path = ''
as $$
declare
    v_remaining integer;
begin
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtext('alert_messages_claim'));

    -- 함수가 중간에 죽어 sending에 멈춘 메일을 10분 뒤 되살린다(설계 6.3).
    update public.alert_messages
    set status = 'pending', claimed_at = null
    where status = 'sending'
      and claimed_at < now() - interval '10 minutes';

    select p_daily_cap
        - (
            select count(*)
            from public.alert_messages as sent
            where sent.status = 'sent'
              and sent.sent_at > now() - interval '24 hours'
        )
        - (
            select count(*)
            from public.alert_messages as in_flight
            where in_flight.status = 'sending'
        )
    into v_remaining;

    if v_remaining <= 0 then
        return;
    end if;

    return query
    update public.alert_messages as message
    set status = 'sending',
        claimed_at = now(),
        attempts = message.attempts + 1
    where message.id in (
        select candidate.id
        from public.alert_messages as candidate
        where candidate.status = 'pending'
          and (p_message_id is null or candidate.id = p_message_id)
        order by
            case candidate.kind when 'confirm' then 0 when 'match' then 1 else 2 end,
            candidate.created_at,
            candidate.id
        limit least(greatest(p_limit, 0), v_remaining)
        for update skip locked
    )
    returning message.*;
end;
$$;

-- 설계 6.2 — 메일 한 통을 만들 내용. 물품은 점수 높은 순, 같으면 최근 등록 순.
create or replace function public.alert_message_payload(p_message_id bigint)
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
    select jsonb_build_object(
        'message', to_jsonb(message),
        'reservation', jsonb_build_object(
            'id', reservation.id,
            'email', reservation.email,
            'query', reservation.query,
            'place_name', reservation.place_name,
            'radius_m', reservation.radius_m,
            'status', reservation.status,
            'link_nonce', reservation.link_nonce,
            'created_at', reservation.created_at,
            'expires_at', reservation.expires_at,
            'extension_count', reservation.extension_count
        ),
        'items', coalesce(
            (
                select jsonb_agg(
                    jsonb_build_object(
                        'id', item.id,
                        'item_name', item.item_name,
                        'raw_category_name', item.raw_category_name,
                        'color_name', item.color_name,
                        'registered_on', item.registered_on,
                        'storage_location_name', location.name,
                        'storage_location_phone', location.phone,
                        'score', notified.score
                    )
                    order by notified.score desc nulls last, item.registered_on desc, item.id desc
                )
                from public.alert_notified_items as notified
                join public.found_items as item on item.id = notified.found_item_id
                left join public.storage_locations as location
                  on location.id = item.storage_location_id
                where notified.message_id = message.id
            ),
            '[]'::jsonb
        )
    )
    from public.alert_messages as message
    join public.alert_reservations as reservation on reservation.id = message.reservation_id
    where message.id = p_message_id
$$;

-- 설계 6.3 — 보낸 결과 기록. sent_at은 DB 시각으로 남겨 24시간 계산과 같은 시계를 쓴다.
create or replace function public.finish_alert_message(
    p_message_id bigint,
    p_status text,
    p_error text default null
)
returns void
language plpgsql
volatile
security invoker
set search_path = ''
as $$
begin
    if p_status not in ('sent', 'pending', 'failed') then
        raise exception 'invalid alert message status: %', p_status;
    end if;

    update public.alert_messages
    set status = p_status,
        sent_at = case when p_status = 'sent' then now() end,
        claimed_at = case when p_status = 'pending' then null else claimed_at end,
        last_error = p_error
    where id = p_message_id
      and status = 'sending';
end;
$$;

-- 설계 6.2 ③ — 만료된 예약과 확인 없이 24시간 지난 예약의 이메일·IP 해시를 지운다. 행은 전환율 집계용으로 남긴다.
create or replace function public.close_ended_alert_reservations()
returns jsonb
language sql
volatile
security invoker
set search_path = ''
as $$
    with expired as (
        update public.alert_reservations
        set status = 'expired', email = null, ip_hash = null, ended_at = now()
        where status = 'active'
          and expires_at <= now()
        returning id
    ),
    unconfirmed as (
        update public.alert_reservations
        set status = 'unconfirmed_expired', email = null, ip_hash = null, ended_at = now()
        where status = 'pending'
          and created_at <= now() - interval '24 hours'
        returning id
    ),
    dropped as (
        delete from public.alert_messages
        where status = 'pending'
          and reservation_id in (select id from expired union all select id from unconfirmed)
        returning id
    )
    select jsonb_build_object(
        'expired', (select count(*) from expired),
        'unconfirmed_closed', (select count(*) from unconfirmed),
        'messages_dropped', (select count(*) from dropped)
    )
$$;

alter table public.alert_reservations enable row level security;
alter table public.alert_match_cursors enable row level security;
alter table public.alert_messages enable row level security;
alter table public.alert_notified_items enable row level security;
alter table public.alert_runs enable row level security;

revoke all on table
    public.alert_reservations,
    public.alert_match_cursors,
    public.alert_messages,
    public.alert_notified_items,
    public.alert_runs
from public, anon, authenticated;

grant select, insert, update, delete on table
    public.alert_reservations,
    public.alert_match_cursors,
    public.alert_messages,
    public.alert_notified_items,
    public.alert_runs
to service_role;

revoke all on sequence
    public.alert_reservations_id_seq,
    public.alert_messages_id_seq,
    public.alert_runs_id_seq
from public, anon, authenticated;

grant usage, select on sequence
    public.alert_reservations_id_seq,
    public.alert_messages_id_seq,
    public.alert_runs_id_seq
to service_role;

revoke all on function public.create_alert_reservation(text, text, text, double precision, double precision, integer, text, text, timestamptz, integer, integer) from public, anon, authenticated;
grant execute on function public.create_alert_reservation(text, text, text, double precision, double precision, integer, text, text, timestamptz, integer, integer) to service_role;
revoke all on function public.confirm_alert_reservation(bigint) from public, anon, authenticated;
grant execute on function public.confirm_alert_reservation(bigint) to service_role;
revoke all on function public.extend_alert_reservation(bigint, integer) from public, anon, authenticated;
grant execute on function public.extend_alert_reservation(bigint, integer) to service_role;
revoke all on function public.cancel_alert_reservation(bigint) from public, anon, authenticated;
grant execute on function public.cancel_alert_reservation(bigint) to service_role;
revoke all on function public.alert_match_queue(text) from public, anon, authenticated;
grant execute on function public.alert_match_queue(text) to service_role;
revoke all on function public.alert_candidate_items(bigint, text) from public, anon, authenticated;
grant execute on function public.alert_candidate_items(bigint, text) to service_role;
revoke all on function public.record_alert_match(bigint, text, timestamptz, jsonb) from public, anon, authenticated;
grant execute on function public.record_alert_match(bigint, text, timestamptz, jsonb) to service_role;
revoke all on function public.enqueue_alert_expiry_notices(interval) from public, anon, authenticated;
grant execute on function public.enqueue_alert_expiry_notices(interval) to service_role;
revoke all on function public.claim_alert_messages(integer, integer, bigint) from public, anon, authenticated;
grant execute on function public.claim_alert_messages(integer, integer, bigint) to service_role;
revoke all on function public.alert_message_payload(bigint) from public, anon, authenticated;
grant execute on function public.alert_message_payload(bigint) to service_role;
revoke all on function public.finish_alert_message(bigint, text, text) from public, anon, authenticated;
grant execute on function public.finish_alert_message(bigint, text, text) to service_role;
revoke all on function public.close_ended_alert_reservations() from public, anon, authenticated;
grant execute on function public.close_ended_alert_reservations() to service_role;
```

- [ ] **Step 7: 테스트가 통과하는지 확인**

Run: `uv run --with 'pytest>=8,<9' pytest tests/database/test_schema_migrations.py -q`
Expected: 모두 PASS. `security invoker` 개수가 12가 아니면 주석에 같은 문구가 들어갔는지 확인한다.

- [ ] **Step 8: DB 함수 수동 검증 스크립트 쓰기**

CI에는 PostGIS가 없어 SQL 문장만 검사한다. 실제 동작은 이 스크립트로 Task 12에서 확인한다. `tests/database/verify_alert_scheduler.sql`을 만든다.

```sql
-- 설계 10.3-1 — 알림 스케줄러 DB 함수 수동 검증.
-- 운영 DB가 아닌 곳(로컬 `supabase start` 후 `supabase db reset`)에서 실행한다.
-- 마지막에 rollback하므로 데이터가 남지 않는다.
-- 실행: psql "$LOCAL_DB_URL" -v ON_ERROR_STOP=1 -f tests/database/verify_alert_scheduler.sql
begin;

do $$
declare
    v_location_source text;
    v_category smallint;
    v_inside bigint;
    v_outside bigint;
    v_created jsonb;
    v_reservation bigint;
    v_expiring bigint;
    v_message bigint;
    v_payload jsonb;
    v_item_inside bigint;
    v_claimed integer;
begin
    select code into v_location_source from public.location_sources order by code limit 1;
    select id into v_category from public.item_categories order by id limit 1;

    -- 서울역(37.5547, 126.9707)에서 북쪽 약 990m(반경 1km 안)와 약 2km(밖)
    insert into public.storage_locations (location_source_code, source_key, name, normalized_name, position)
    values (
        v_location_source, 'verify-inside', '검증 안쪽', '검증안쪽',
        extensions.st_setsrid(extensions.st_makepoint(126.9707, 37.5636), 4326)::extensions.geography
    )
    returning id into v_inside;
    insert into public.storage_locations (location_source_code, source_key, name, normalized_name, position)
    values (
        v_location_source, 'verify-outside', '검증 바깥', '검증바깥',
        extensions.st_setsrid(extensions.st_makepoint(126.9707, 37.5727), 4326)::extensions.geography
    )
    returning id into v_outside;

    v_created := public.create_alert_reservation(
        'verify@example.com', '에어팟', '서울역', 37.5547, 126.9707, 1000,
        'verify-ip', 'verify-nonce', now() + interval '7 days'
    );
    if v_created ->> 'status' <> 'created' then
        raise exception '예약 생성 실패: %', v_created;
    end if;
    v_reservation := (v_created ->> 'reservation_id')::bigint;
    if public.confirm_alert_reservation(v_reservation) <> 'confirmed' then
        raise exception '확인 실패';
    end if;

    -- 같은 트랜잭션 안에서는 now()가 같아서 커서를 1분 앞당겨 "예약 이후 물품"을 만든다.
    update public.alert_match_cursors
    set checked_until = now() - interval '1 minute'
    where reservation_id = v_reservation;

    insert into public.found_items (
        item_source_code, atc_id, found_sequence, storage_location_id, location_match_status,
        category_id, raw_category_name, item_name, registered_on
    )
    values ('partner_api', 'verify-1', '1', v_inside, 'matched', v_category, '무선이어폰', '에어팟', current_date)
    returning id into v_item_inside;
    insert into public.found_items (
        item_source_code, atc_id, found_sequence, storage_location_id, location_match_status,
        category_id, raw_category_name, item_name, registered_on
    )
    values
        ('partner_api', 'verify-2', '1', v_outside, 'matched', v_category, '무선이어폰', '에어팟', current_date),
        ('partner_api', 'verify-3', '1', null, 'unmatched', v_category, '무선이어폰', '에어팟', current_date);

    -- 반경 안 + 매칭된 물품 1건만 후보가 된다.
    v_payload := public.alert_candidate_items(v_reservation, 'partner_api');
    if jsonb_array_length(v_payload -> 'items') <> 1
       or (v_payload -> 'items' -> 0 ->> 'id')::bigint <> v_item_inside then
        raise exception '반경·매칭 조건이 틀렸습니다: %', v_payload;
    end if;

    v_payload := public.record_alert_match(
        v_reservation, 'partner_api', (v_payload ->> 'checked_until')::timestamptz,
        jsonb_build_array(jsonb_build_object('found_item_id', v_item_inside, 'score', 1.0))
    );
    if (v_payload ->> 'items_queued')::integer <> 1 then
        raise exception '알림 물품 기록 실패: %', v_payload;
    end if;
    v_payload := public.record_alert_match(
        v_reservation, 'partner_api', now(),
        jsonb_build_array(jsonb_build_object('found_item_id', v_item_inside, 'score', 1.0))
    );
    if (v_payload ->> 'items_queued')::integer <> 0 then
        raise exception '같은 물품이 두 번 기록됐습니다';
    end if;
    if jsonb_array_length(public.alert_candidate_items(v_reservation, 'partner_api') -> 'items') <> 0 then
        raise exception '커서가 움직이지 않았습니다';
    end if;

    -- 한도 1이면 한 통만 집고(확인 메일이 먼저), 보낸 뒤에는 24시간 동안 더 집지 않는다.
    select count(*) into v_claimed from public.claim_alert_messages(10, 1);
    if v_claimed <> 1 then
        raise exception '한도 1인데 %통을 집었습니다', v_claimed;
    end if;
    select id into v_message from public.alert_messages where status = 'sending' limit 1;
    perform public.finish_alert_message(v_message, 'sent', null);
    select count(*) into v_claimed from public.claim_alert_messages(10, 1);
    if v_claimed <> 0 then
        raise exception '최근 24시간 발송 수를 세지 않았습니다';
    end if;

    select id into v_message
    from public.alert_messages
    where reservation_id = v_reservation and kind = 'match';
    v_payload := public.alert_message_payload(v_message);
    if jsonb_array_length(v_payload -> 'items') <> 1
       or v_payload -> 'reservation' ->> 'email' <> 'verify@example.com' then
        raise exception '메일 내용 조회 실패: %', v_payload;
    end if;

    -- 만료 안내는 만료일마다 한 번, 만료되면 이메일이 지워진다.
    v_created := public.create_alert_reservation(
        'expiring@example.com', '지갑', '서울역', 37.5547, 126.9707, 1000,
        null, 'verify-nonce-2', now() + interval '24 hours'
    );
    v_expiring := (v_created ->> 'reservation_id')::bigint;
    perform public.confirm_alert_reservation(v_expiring);
    if public.enqueue_alert_expiry_notices() <> 1 then
        raise exception '만료 안내가 들어가지 않았습니다';
    end if;
    if public.enqueue_alert_expiry_notices() <> 0 then
        raise exception '같은 만료일 안내가 두 번 들어갔습니다';
    end if;
    update public.alert_reservations set expires_at = now() - interval '1 minute' where id = v_expiring;
    v_payload := public.close_ended_alert_reservations();
    if (v_payload ->> 'expired')::integer <> 1
       or exists (select 1 from public.alert_reservations where id = v_expiring and email is not null) then
        raise exception '만료 정리 실패: %', v_payload;
    end if;

    -- 취소하면 이메일이 지워지고 대기 메일이 사라진다.
    if public.cancel_alert_reservation(v_reservation) <> 'cancelled' then
        raise exception '취소 실패';
    end if;
    if exists (select 1 from public.alert_reservations where id = v_reservation and email is not null)
       or exists (select 1 from public.alert_messages where reservation_id = v_reservation and status = 'pending') then
        raise exception '취소 뒤 개인정보나 대기 메일이 남았습니다';
    end if;

    raise notice '알림 스케줄러 DB 검증 통과';
end;
$$;

rollback;
```

- [ ] **Step 9: 커밋**

```bash
git add supabase/migrations/20261106120000_create_alert_scheduler.sql tests/database/verify_alert_scheduler.sql tests/database/test_schema_migrations.py
git commit -m "feat: 알림 예약 테이블·DB 함수 마이그레이션 — PRD 2.1-4, 4.2 P1" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: 설정·오류·링크 토큰·만료 시각

**Files:**
- Create: `src/retriever_lost_found/alerts/__init__.py`, `errors.py`, `settings.py`, `links.py`
- Create: `tests/alerts/alert_fakes.py` (이후 Task 3·6에서 이어 붙인다)
- Test: `tests/alerts/test_alert_settings.py`, `tests/alerts/test_alert_links.py`

**Interfaces:**
- Produces:
  - `errors.AlertLimitError(Exception)`, `errors.safe_error(error: BaseException, secrets: Iterable[str] = ()) -> str`
  - `settings.MailSettings(host, port, username, password, from_address)`, `settings.AlertSettings(mail, public_base_url, secret, daily_send_limit=450)`, `settings.REQUIRED_NAMES`, `settings.DEFAULT_DAILY_SEND_LIMIT`, `settings.load_alert_settings(env_path: Path = DEFAULT_ENV_FILE) -> AlertSettings | None`
  - `links.KOREA_TIMEZONE`, `RESERVATION_DAYS=7`, `EXTENSION_DAYS=7`, `MAX_EXTENSIONS=3`, `EXPIRY_HOUR_KST=11`, `LINK_PATHS`
  - `links.MailLinks(home, cancel, confirm=None, extend=None)`
  - `links.new_link_nonce() -> str`, `links.link_token(secret, reservation_id, nonce) -> str`, `links.token_reservation_id(token) -> int`, `links.token_matches(secret, token, nonce) -> bool`, `links.ip_hash(secret, ip) -> str | None`, `links.expires_at_for(created_at: datetime) -> datetime`, `links.parse_timestamp(value: str | datetime) -> datetime`, `links.mail_links(settings, reservation: dict, now: datetime) -> MailLinks`
  - 테스트용(`tests/alerts/alert_fakes.py`): `NOW`, `TEST_SETTINGS`, `reservation_row(**overrides) -> dict`

- [ ] **Step 1: 테스트 도우미 만들기**

`tests/alerts/alert_fakes.py`:

```python
"""③ 스케줄러 테스트용 설정·가짜 저장소·가짜 발송기. 외부 서비스를 부르지 않는다(AGENTS.md 7장)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from retriever_lost_found.alerts.settings import AlertSettings, MailSettings

# 2026-11-06 15:00 KST
NOW = datetime(2026, 11, 6, 6, 0, tzinfo=timezone.utc)

TEST_SETTINGS = AlertSettings(
    mail=MailSettings(
        host="smtp.example.com",
        port=465,
        username="sender@example.com",
        password="app-password-1234",
        from_address="리트리버 <sender@example.com>",
    ),
    public_base_url="https://retriever.example",
    secret="alert-test-secret",
    daily_send_limit=450,
)


def reservation_row(**overrides: Any) -> dict[str, Any]:
    """DB(REST·RPC)가 돌려주는 모양의 예약 행. 시각은 ISO 문자열이다."""
    row: dict[str, Any] = {
        "id": 7,
        "email": "user@example.com",
        "query": "에어팟",
        "place_name": "서울역",
        "radius_m": 3000,
        "status": "active",
        "link_nonce": "nonce-7",
        "created_at": NOW.isoformat(),
        "expires_at": "2026-11-13T02:00:00+00:00",
        "extension_count": 0,
    }
    row.update(overrides)
    return row
```

- [ ] **Step 2: 실패하는 테스트 쓰기**

`tests/alerts/test_alert_settings.py`:

```python
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from retriever_lost_found.alerts.errors import safe_error
from retriever_lost_found.alerts.settings import (
    DEFAULT_DAILY_SEND_LIMIT,
    REQUIRED_NAMES,
    load_alert_settings,
)

VALUES = {
    "MAIL_SMTP_HOST": "smtp.gmail.com",
    "MAIL_SMTP_PORT": "465",
    "MAIL_SMTP_USERNAME": "sender@example.com",
    "MAIL_SMTP_PASSWORD": "app-password",
    "MAIL_FROM": "리트리버 <sender@example.com>",
    "PUBLIC_BASE_URL": "https://retriever.example/",
    "ALERT_SECRET": "alert-secret",
    "ALERT_DAILY_SEND_LIMIT": "",
}
# 존재하지 않는 파일 — 개발자 PC의 실제 .env를 읽지 않게 한다.
MISSING_ENV_FILE = Path(tempfile.gettempdir()) / "retriever-alert-settings-missing.env"


class AlertSettingsTests(unittest.TestCase):
    def load(self, **overrides: str):
        with patch.dict(os.environ, {**VALUES, **overrides}):
            return load_alert_settings(MISSING_ENV_FILE)

    def test_reads_mail_settings_with_the_450_default(self) -> None:
        settings = self.load()

        self.assertIsNotNone(settings)
        self.assertEqual(settings.mail.port, 465)
        self.assertEqual(settings.public_base_url, "https://retriever.example")
        self.assertEqual(settings.daily_send_limit, DEFAULT_DAILY_SEND_LIMIT)
        self.assertEqual(DEFAULT_DAILY_SEND_LIMIT, 450)

    def test_any_missing_value_turns_alerts_off(self) -> None:
        for name in REQUIRED_NAMES:
            with self.subTest(name=name):
                self.assertIsNone(self.load(**{name: ""}))

    def test_only_ssl_or_starttls_ports(self) -> None:
        self.assertEqual(self.load(MAIL_SMTP_PORT="587").mail.port, 587)
        with self.assertRaisesRegex(ValueError, "465 또는 587"):
            self.load(MAIL_SMTP_PORT="25")

    def test_daily_limit_stays_under_the_gmail_limit(self) -> None:
        self.assertEqual(self.load(ALERT_DAILY_SEND_LIMIT="300").daily_send_limit, 300)
        with self.assertRaisesRegex(ValueError, "1~500"):
            self.load(ALERT_DAILY_SEND_LIMIT="600")


class SafeErrorTests(unittest.TestCase):
    def test_hides_secrets_and_keeps_the_rest(self) -> None:
        message = safe_error(RuntimeError("login failed for app-password"), ("app-password", ""))

        self.assertNotIn("app-password", message)
        self.assertIn("[REDACTED]", message)
        self.assertIn("RuntimeError: login failed for", message)


if __name__ == "__main__":
    unittest.main()
```

`tests/alerts/test_alert_links.py`:

```python
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from alert_fakes import NOW, TEST_SETTINGS, reservation_row
from retriever_lost_found.alerts.links import (
    KOREA_TIMEZONE,
    expires_at_for,
    ip_hash,
    link_token,
    mail_links,
    new_link_nonce,
    token_matches,
    token_reservation_id,
)


class LinkTokenTests(unittest.TestCase):
    def test_token_round_trips_and_rejects_tampering(self) -> None:
        token = link_token("secret", 7, "nonce-7")

        self.assertTrue(token.startswith("7."))
        self.assertTrue(token_matches("secret", token, "nonce-7"))
        self.assertFalse(token_matches("secret", token, "other-nonce"))
        self.assertFalse(token_matches("other-secret", token, "nonce-7"))
        tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
        self.assertFalse(token_matches("secret", tampered, "nonce-7"))

    def test_malformed_token_is_a_bad_link(self) -> None:
        for token in ("", "abc", "7.", "0.0123456789abcdef0123456789abcdef", "7.XYZ"):
            with self.subTest(token=token), self.assertRaisesRegex(ValueError, "링크가 올바르지 않습니다"):
                token_reservation_id(token)

    def test_nonce_is_random(self) -> None:
        first = new_link_nonce()

        self.assertEqual(len(first), 32)
        self.assertNotEqual(first, new_link_nonce())


class IpHashTests(unittest.TestCase):
    def test_same_ip_same_hash_and_the_raw_ip_is_not_kept(self) -> None:
        first = ip_hash("secret", "203.0.113.7")

        self.assertEqual(first, ip_hash("secret", " 203.0.113.7 "))
        self.assertNotIn("203.0.113.7", first)
        self.assertNotEqual(first, ip_hash("other-secret", "203.0.113.7"))

    def test_missing_ip_is_not_hashed(self) -> None:
        self.assertIsNone(ip_hash("secret", "  "))


class ExpiryTests(unittest.TestCase):
    def test_expiry_is_eleven_kst_seven_days_after_registration(self) -> None:
        # 11-06 15:00 KST 등록 → 11-13 11:00 KST (설계 5.3)
        registered = datetime(2026, 11, 6, 6, 0, tzinfo=timezone.utc)

        self.assertEqual(
            expires_at_for(registered),
            datetime(2026, 11, 13, 11, 0, tzinfo=KOREA_TIMEZONE),
        )

    def test_expiry_counts_from_the_korean_date_not_the_utc_date(self) -> None:
        # 11-06 00:30 KST는 UTC로 아직 11-05다. 한국 날짜 기준이면 11-13, UTC 기준이면 11-12가 된다.
        registered = datetime(2026, 11, 5, 15, 30, tzinfo=timezone.utc)

        self.assertEqual(
            expires_at_for(registered),
            datetime(2026, 11, 13, 11, 0, tzinfo=KOREA_TIMEZONE),
        )


class MailLinksTests(unittest.TestCase):
    def test_pending_reservation_gets_confirm_and_cancel_links_only(self) -> None:
        links = mail_links(TEST_SETTINGS, reservation_row(status="pending"), NOW)
        token = link_token(TEST_SETTINGS.secret, 7, "nonce-7")

        self.assertEqual(links.confirm, f"https://retriever.example/alerts/confirm?token={token}")
        self.assertEqual(links.cancel, f"https://retriever.example/alerts/cancel?token={token}")
        self.assertEqual(links.home, "https://retriever.example/")
        self.assertIsNone(links.extend)

    def test_extend_link_only_while_extensions_remain(self) -> None:
        self.assertIsNotNone(mail_links(TEST_SETTINGS, reservation_row(extension_count=2), NOW).extend)
        self.assertIsNone(mail_links(TEST_SETTINGS, reservation_row(extension_count=3), NOW).extend)
        self.assertIsNone(mail_links(TEST_SETTINGS, reservation_row(), NOW).confirm)

    def test_no_extend_link_after_expiry(self) -> None:
        expired = reservation_row(expires_at=(NOW - timedelta(minutes=1)).isoformat())

        self.assertIsNone(mail_links(TEST_SETTINGS, expired, NOW).extend)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 테스트가 실패하는지 확인**

Run: `uv run --with 'pytest>=8,<9' pytest tests/alerts -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'retriever_lost_found.alerts'`

- [ ] **Step 4: 구현 쓰기**

`src/retriever_lost_found/alerts/__init__.py`:

```python
"""③ 스케줄러(분실물 알림 예약) — PRD 2.1-4, 4.2 P1. 설계 docs/alert-scheduler/."""
```

`src/retriever_lost_found/alerts/errors.py`:

```python
from __future__ import annotations

from collections.abc import Iterable


class AlertLimitError(Exception):
    """예약 제한·발송 한도에 걸림 — API는 429로 돌려준다(설계 5.4)."""


def safe_error(error: BaseException, secrets: Iterable[str] = ()) -> str:
    """오류 문구에서 비밀값을 가리고 1,000자로 자른다(설계 8장)."""
    message = f"{type(error).__name__}: {error}"
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return message[:1000]
```

`src/retriever_lost_found/alerts/settings.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import DEFAULT_ENV_FILE, read_optional_env_value

# 설계 2장 — Gmail 개인 계정 하루 약 500통보다 낮게 잡은 상한(2026-11-06 결정)
DEFAULT_DAILY_SEND_LIMIT = 450
REQUIRED_NAMES = (
    "MAIL_SMTP_HOST",
    "MAIL_SMTP_PORT",
    "MAIL_SMTP_USERNAME",
    "MAIL_SMTP_PASSWORD",
    "MAIL_FROM",
    "PUBLIC_BASE_URL",
    "ALERT_SECRET",
)


@dataclass(frozen=True)
class MailSettings:
    host: str
    port: int
    username: str
    password: str
    from_address: str


@dataclass(frozen=True)
class AlertSettings:
    mail: MailSettings
    public_base_url: str
    secret: str
    daily_send_limit: int = DEFAULT_DAILY_SEND_LIMIT


def load_alert_settings(env_path: Path = DEFAULT_ENV_FILE) -> AlertSettings | None:
    """하나라도 없으면 None — 예약 API가 503을 돌려준다(Preview에서 실제 메일 차단, 설계 8장)."""
    values = {name: read_optional_env_value(name, env_path) for name in REQUIRED_NAMES}
    if not all(values.values()):
        return None
    try:
        port = int(values["MAIL_SMTP_PORT"])
    except ValueError:
        raise ValueError("MAIL_SMTP_PORT는 숫자여야 합니다.") from None
    if port not in (465, 587):
        raise ValueError("MAIL_SMTP_PORT는 465 또는 587이어야 합니다.")
    raw_limit = read_optional_env_value("ALERT_DAILY_SEND_LIMIT", env_path)
    try:
        daily_send_limit = int(raw_limit) if raw_limit else DEFAULT_DAILY_SEND_LIMIT
    except ValueError:
        raise ValueError("ALERT_DAILY_SEND_LIMIT는 숫자여야 합니다.") from None
    if not 1 <= daily_send_limit <= 500:
        raise ValueError("ALERT_DAILY_SEND_LIMIT는 1~500 사이여야 합니다.")
    return AlertSettings(
        mail=MailSettings(
            host=values["MAIL_SMTP_HOST"],
            port=port,
            username=values["MAIL_SMTP_USERNAME"],
            password=values["MAIL_SMTP_PASSWORD"],
            from_address=values["MAIL_FROM"],
        ),
        public_base_url=values["PUBLIC_BASE_URL"].rstrip("/"),
        secret=values["ALERT_SECRET"],
        daily_send_limit=daily_send_limit,
    )
```

`src/retriever_lost_found/alerts/links.py`:

```python
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from typing import Any

from .settings import AlertSettings

KOREA_TIMEZONE = timezone(timedelta(hours=9))
RESERVATION_DAYS = 7  # PRD 4.2 P1 — 등록한 날부터 1주일
EXTENSION_DAYS = 7  # 설계 2장 — 연장 한 번에 +7일
MAX_EXTENSIONS = 3  # 설계 2장 — 최대 3회(최장 4주)
EXPIRY_HOUR_KST = 11  # 설계 5.3 — 발송 크론(KST 11시대)과 맞춘다
# 메일 속 링크가 여는 프런트 경로. 강지현과 합의가 남았다(설계 13장) — 바뀌면 여기만 고친다.
LINK_PATHS = {
    "confirm": "/alerts/confirm",
    "extend": "/alerts/extend",
    "cancel": "/alerts/cancel",
}
_TOKEN_PATTERN = re.compile(r"([1-9]\d{0,17})\.([0-9a-f]{32})")


@dataclass(frozen=True)
class MailLinks:
    home: str
    cancel: str
    confirm: str | None = None
    extend: str | None = None


def new_link_nonce() -> str:
    return secrets.token_hex(16)


def link_token(secret: str, reservation_id: int, nonce: str) -> str:
    """설계 5.1 — DB에 저장하지 않고 메일을 보낼 때마다 다시 만든다."""
    digest = hmac.new(
        secret.encode("utf-8"),
        f"link:{reservation_id}:{nonce}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{reservation_id}.{digest[:32]}"


def token_reservation_id(token: str) -> int:
    match = _TOKEN_PATTERN.fullmatch(token.strip())
    if match is None:
        raise ValueError("링크가 올바르지 않습니다.")
    return int(match.group(1))


def token_matches(secret: str, token: str, nonce: str) -> bool:
    reservation_id = token_reservation_id(token)
    return hmac.compare_digest(link_token(secret, reservation_id, nonce), token.strip())


def ip_hash(secret: str, ip: str) -> str | None:
    """설계 5.2 — IP 원문은 저장하지 않는다."""
    ip = ip.strip()
    if not ip:
        return None
    return hmac.new(secret.encode("utf-8"), f"ip:{ip}".encode("utf-8"), hashlib.sha256).hexdigest()


def expires_at_for(created_at: datetime) -> datetime:
    """설계 5.3 — 등록한 날(KST 날짜) + 7일의 11:00 KST."""
    local_date = created_at.astimezone(KOREA_TIMEZONE).date()
    return datetime.combine(
        local_date + timedelta(days=RESERVATION_DAYS),
        time(EXPIRY_HOUR_KST),
        tzinfo=KOREA_TIMEZONE,
    )


def parse_timestamp(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def mail_links(settings: AlertSettings, reservation: dict[str, Any], now: datetime) -> MailLinks:
    token = link_token(settings.secret, int(reservation["id"]), str(reservation["link_nonce"]))
    base = settings.public_base_url

    def url(action: str) -> str:
        return f"{base}{LINK_PATHS[action]}?token={token}"

    can_extend = (
        reservation["status"] == "active"
        and int(reservation["extension_count"]) < MAX_EXTENSIONS
        and parse_timestamp(reservation["expires_at"]) > now
    )
    return MailLinks(
        home=f"{base}/",
        cancel=url("cancel"),
        confirm=url("confirm") if reservation["status"] == "pending" else None,
        extend=url("extend") if can_extend else None,
    )
```

- [ ] **Step 5: 테스트가 통과하는지 확인**

Run: `uv run --with 'pytest>=8,<9' pytest tests/alerts -q`
Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add src/retriever_lost_found/alerts tests/alerts
git commit -m "feat: 알림 설정·링크 토큰·만료 시각 — PRD 4.2 P1" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: SMTP 발송과 오류 분류

**Files:**
- Create: `src/retriever_lost_found/alerts/sender.py`
- Modify: `tests/alerts/alert_fakes.py` (끝에 `FakeEmailSender` 추가)
- Test: `tests/alerts/test_alert_sender.py`

**Interfaces:**
- Consumes: `settings.MailSettings`
- Produces:
  - `OutgoingEmail(to: str, subject: str, text: str, html: str)`
  - 오류: `SendError` ← `DailyLimitExceeded`, `PermanentSendError`, `TemporarySendError`, `LoginFailed`
  - `EmailSession`(Protocol: `send(email) -> None`, `close() -> None`), `EmailSender`(Protocol: `open() -> EmailSession`)
  - `classify_smtp_error(code: int, response: bytes | str) -> SendError`
  - `SmtpEmailSender(settings: MailSettings, *, timeout=20.0).open() -> SmtpSession`
  - 테스트용: `FakeEmailSender(*, failures: dict[str, list[SendError]] | None = None, open_error: SendError | None = None)` — `.sent: list[OutgoingEmail]`, `.open_count`, `.closed`

- [ ] **Step 1: 실패하는 테스트 쓰기**

`tests/alerts/test_alert_sender.py`:

```python
from __future__ import annotations

import smtplib
import unittest
from dataclasses import replace
from unittest.mock import patch

from alert_fakes import TEST_SETTINGS
from retriever_lost_found.alerts.sender import (
    DailyLimitExceeded,
    LoginFailed,
    OutgoingEmail,
    PermanentSendError,
    SmtpEmailSender,
    TemporarySendError,
    classify_smtp_error,
)

EMAIL = OutgoingEmail(to="user@example.com", subject="[리트리버] 테스트", text="본문", html="<p>본문</p>")


class FakeSmtp:
    instances: list["FakeSmtp"] = []
    login_error: Exception | None = None
    send_error: Exception | None = None

    def __init__(self, host, port, timeout=None, context=None) -> None:
        self.host = host
        self.port = port
        self.started_tls = False
        self.logged_in = None
        self.messages = []
        self.quit_called = False
        FakeSmtp.instances.append(self)

    def starttls(self, context=None) -> None:
        self.started_tls = True

    def login(self, username, password) -> None:
        if FakeSmtp.login_error is not None:
            raise FakeSmtp.login_error
        self.logged_in = (username, password)

    def send_message(self, message):
        if FakeSmtp.send_error is not None:
            raise FakeSmtp.send_error
        self.messages.append(message)
        return {}

    def quit(self) -> None:
        self.quit_called = True


class SmtpSenderTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeSmtp.instances = []
        FakeSmtp.login_error = None
        FakeSmtp.send_error = None

    def open_session(self, mail=TEST_SETTINGS.mail):
        with (
            patch("retriever_lost_found.alerts.sender.smtplib.SMTP_SSL", FakeSmtp),
            patch("retriever_lost_found.alerts.sender.smtplib.SMTP", FakeSmtp),
        ):
            return SmtpEmailSender(mail).open()

    def test_port_465_logs_in_over_ssl_and_sends_text_and_html(self) -> None:
        session = self.open_session()
        session.send(EMAIL)
        session.close()

        client = FakeSmtp.instances[0]
        self.assertEqual((client.host, client.port), ("smtp.example.com", 465))
        self.assertFalse(client.started_tls)
        self.assertEqual(client.logged_in, ("sender@example.com", "app-password-1234"))
        message = client.messages[0]
        self.assertEqual(message["To"], "user@example.com")
        self.assertEqual(message["From"], TEST_SETTINGS.mail.from_address)
        self.assertEqual(message["Subject"], "[리트리버] 테스트")
        self.assertEqual(
            [part.get_content_type() for part in message.iter_parts()],
            ["text/plain", "text/html"],
        )
        self.assertTrue(client.quit_called)

    def test_port_587_upgrades_with_starttls(self) -> None:
        self.open_session(replace(TEST_SETTINGS.mail, port=587))

        self.assertTrue(FakeSmtp.instances[0].started_tls)

    def test_login_failure_stops_the_run_without_leaking_the_password(self) -> None:
        FakeSmtp.login_error = smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")

        with self.assertRaises(LoginFailed) as caught:
            self.open_session()

        self.assertNotIn("app-password-1234", str(caught.exception))

    def test_gmail_daily_limit_is_its_own_error(self) -> None:
        session = self.open_session()
        FakeSmtp.send_error = smtplib.SMTPDataError(550, b"5.4.5 Daily user sending limit exceeded.")

        with self.assertRaises(DailyLimitExceeded):
            session.send(EMAIL)

    def test_other_5xx_fails_only_this_mail(self) -> None:
        session = self.open_session()
        FakeSmtp.send_error = smtplib.SMTPRecipientsRefused(
            {"user@example.com": (550, b"5.1.1 The email account does not exist")}
        )

        with self.assertRaises(PermanentSendError):
            session.send(EMAIL)

    def test_4xx_and_disconnects_are_temporary(self) -> None:
        session = self.open_session()
        FakeSmtp.send_error = smtplib.SMTPDataError(421, b"4.7.0 Try again later")
        with self.assertRaises(TemporarySendError):
            session.send(EMAIL)

        FakeSmtp.send_error = smtplib.SMTPServerDisconnected("Connection unexpectedly closed")
        with self.assertRaises(TemporarySendError):
            session.send(EMAIL)

    def test_classifier_keeps_the_daily_limit_apart_from_other_550(self) -> None:
        self.assertIsInstance(classify_smtp_error(550, b"5.4.5 Daily user sending limit exceeded"), DailyLimitExceeded)
        self.assertIsInstance(classify_smtp_error(550, "5.1.1 no such user"), PermanentSendError)
        self.assertIsInstance(classify_smtp_error(451, b"4.3.0 try later"), TemporarySendError)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `uv run --with 'pytest>=8,<9' pytest tests/alerts/test_alert_sender.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'retriever_lost_found.alerts.sender'`

- [ ] **Step 3: 구현 쓰기**

`src/retriever_lost_found/alerts/sender.py`:

```python
from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

from .settings import MailSettings


@dataclass(frozen=True)
class OutgoingEmail:
    to: str
    subject: str
    text: str
    html: str


class SendError(Exception):
    """메일 한 통을 보내지 못함."""


class DailyLimitExceeded(SendError):
    """Gmail 550 5.4.5 — 오늘은 더 보내지 않는다(설계 6.3)."""


class PermanentSendError(SendError):
    """5xx — 이 메일만 실패로 두고 다시 보내지 않는다."""


class TemporarySendError(SendError):
    """4xx·연결 오류 — 다음 실행에서 다시 보낸다."""


class LoginFailed(SendError):
    """SMTP 로그인 실패(앱 비밀번호 무효 등) — 이번 실행 전체를 멈춘다."""


class EmailSession(Protocol):
    def send(self, email: OutgoingEmail) -> None: ...

    def close(self) -> None: ...


class EmailSender(Protocol):
    def open(self) -> EmailSession: ...


def classify_smtp_error(code: int, response: bytes | str) -> SendError:
    text = response.decode("utf-8", errors="replace") if isinstance(response, bytes) else str(response)
    if code == 550 and "5.4.5" in text:
        return DailyLimitExceeded(f"SMTP {code} 하루 발송 한도 초과")
    if 500 <= code < 600:
        return PermanentSendError(f"SMTP {code}")
    return TemporarySendError(f"SMTP {code}")


class SmtpSession:
    def __init__(self, client: smtplib.SMTP, from_address: str) -> None:
        self.client = client
        self.from_address = from_address

    def send(self, email: OutgoingEmail) -> None:
        message = EmailMessage()
        message["Subject"] = email.subject
        message["From"] = self.from_address
        message["To"] = email.to
        message.set_content(email.text)
        message.add_alternative(email.html, subtype="html")
        try:
            refused = self.client.send_message(message)
        except smtplib.SMTPRecipientsRefused as error:
            code, response = next(iter(error.recipients.values()))
            raise classify_smtp_error(code, response) from None
        except smtplib.SMTPResponseException as error:
            raise classify_smtp_error(error.smtp_code, error.smtp_error) from None
        except (smtplib.SMTPException, OSError) as error:
            raise TemporarySendError(f"SMTP 전송 실패: {type(error).__name__}") from None
        if refused:
            code, response = next(iter(refused.values()))
            raise classify_smtp_error(code, response)

    def close(self) -> None:
        try:
            self.client.quit()
        except (smtplib.SMTPException, OSError):
            pass


class SmtpEmailSender:
    """설계 3.1 — Gmail 앱 비밀번호 SMTP. 465는 SSL, 587은 STARTTLS."""

    def __init__(self, settings: MailSettings, *, timeout: float = 20.0) -> None:
        self.settings = settings
        self.timeout = timeout

    def open(self) -> SmtpSession:
        settings = self.settings
        context = ssl.create_default_context()
        try:
            if settings.port == 465:
                client = smtplib.SMTP_SSL(settings.host, settings.port, timeout=self.timeout, context=context)
            else:
                client = smtplib.SMTP(settings.host, settings.port, timeout=self.timeout)
                client.starttls(context=context)
            client.login(settings.username, settings.password)
        except smtplib.SMTPAuthenticationError as error:
            # 앱 비밀번호가 오류 문구에 섞이지 않게 코드만 남긴다(설계 8장).
            raise LoginFailed(f"SMTP 로그인 실패 {error.smtp_code}") from None
        except smtplib.SMTPResponseException as error:
            raise classify_smtp_error(error.smtp_code, error.smtp_error) from None
        except (smtplib.SMTPException, OSError) as error:
            raise TemporarySendError(f"SMTP 연결 실패: {type(error).__name__}") from None
        return SmtpSession(client, settings.from_address)
```

`tests/alerts/alert_fakes.py` 끝에 이어 붙인다.

```python
from retriever_lost_found.alerts.sender import OutgoingEmail, SendError  # noqa: E402


class FakeEmailSender:
    """보낸 메일을 기록한다. failures[수신자]에 넣은 오류를 차례로 하나씩 던진다."""

    def __init__(
        self,
        *,
        failures: dict[str, list[SendError]] | None = None,
        open_error: SendError | None = None,
    ) -> None:
        self.sent: list[OutgoingEmail] = []
        self.failures = {address: list(errors) for address, errors in (failures or {}).items()}
        self.open_error = open_error
        self.open_count = 0
        self.closed = 0

    def open(self) -> "FakeEmailSender":
        if self.open_error is not None:
            raise self.open_error
        self.open_count += 1
        return self

    def send(self, email: OutgoingEmail) -> None:
        queued = self.failures.get(email.to)
        if queued:
            raise queued.pop(0)
        self.sent.append(email)

    def close(self) -> None:
        self.closed += 1
```

- [ ] **Step 4: 테스트가 통과하는지 확인**

Run: `uv run --with 'pytest>=8,<9' pytest tests/alerts -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add src/retriever_lost_found/alerts/sender.py tests/alerts/alert_fakes.py tests/alerts/test_alert_sender.py
git commit -m "feat: Gmail SMTP 발송과 오류 분류 — PRD 4.2 P1" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: 메일 본문 3종

**Files:**
- Create: `src/retriever_lost_found/alerts/templates.py`
- Test: `tests/alerts/test_alert_templates.py`

**Interfaces:**
- Consumes: `links.MailLinks`, `links.KOREA_TIMEZONE`, `links.MAX_EXTENSIONS`, `links.parse_timestamp`, `sender.OutgoingEmail`
- Produces:
  - 상수 `MAX_ITEMS_PER_MAIL=5`, `CONFIRM_SUBJECT`, `MATCH_SUBJECT`, `EXPIRY_SUBJECT`, `MATCH_HEADLINE`, `OWNERSHIP_NOTICE`
  - `format_expiry(value: str | datetime) -> str` 예: `"11월 13일(금) 오전 11시"`
  - `confirmation_email(to: str, reservation: dict, links: MailLinks) -> OutgoingEmail`
  - `match_email(to: str, reservation: dict, links: MailLinks, items: list[dict]) -> OutgoingEmail` — `items`는 이미 정렬된 목록(점수 높은 순)
  - `expiry_notice_email(to: str, reservation: dict, links: MailLinks) -> OutgoingEmail`
  - `reservation`에 필요한 키: `query`, `place_name`, `radius_m`, `expires_at`, `extension_count`
  - `items` 원소 키: `item_name`, `raw_category_name`, `color_name`, `registered_on`, `storage_location_name`, `storage_location_phone`

- [ ] **Step 1: 실패하는 테스트 쓰기**

`tests/alerts/test_alert_templates.py`:

```python
from __future__ import annotations

import unittest

from alert_fakes import NOW, TEST_SETTINGS, reservation_row
from retriever_lost_found.alerts.links import mail_links
from retriever_lost_found.alerts.templates import (
    CONFIRM_SUBJECT,
    EXPIRY_SUBJECT,
    MATCH_HEADLINE,
    MATCH_SUBJECT,
    OWNERSHIP_NOTICE,
    confirmation_email,
    expiry_notice_email,
    format_expiry,
    match_email,
)


def item(index: int) -> dict:
    return {
        "id": index,
        "item_name": f"에어팟 {index}",
        "raw_category_name": "무선이어폰",
        "color_name": "흰색",
        "registered_on": "2026-11-06",
        "storage_location_name": "서울역 유실물센터",
        "storage_location_phone": "02-000-0000",
        "score": 1.0,
    }


def links_for(reservation: dict):
    return mail_links(TEST_SETTINGS, reservation, NOW)


class TemplateTests(unittest.TestCase):
    def test_expiry_is_written_in_korean_time(self) -> None:
        self.assertEqual(format_expiry("2026-11-13T02:00:00+00:00"), "11월 13일(금) 오전 11시")

    def test_confirmation_mail_has_confirm_and_cancel_links(self) -> None:
        reservation = reservation_row(status="pending")
        links = links_for(reservation)

        email = confirmation_email("user@example.com", reservation, links)

        self.assertEqual(email.subject, CONFIRM_SUBJECT)
        self.assertIn(links.confirm, email.text)
        self.assertIn(links.cancel, email.text)
        self.assertIn("24시간", email.text)
        self.assertIn("서울역 반경 3km", email.text)

    def test_match_mail_says_similar_and_does_not_claim_ownership(self) -> None:
        reservation = reservation_row()

        email = match_email("user@example.com", reservation, links_for(reservation), [item(1)])

        self.assertEqual(email.subject, MATCH_SUBJECT)
        self.assertIn("유사한 물품이 새로 등록됐습니다", email.text)
        self.assertIn(MATCH_HEADLINE, email.text)
        self.assertIn(OWNERSHIP_NOTICE, email.text)
        self.assertIn("서울역 유실물센터 02-000-0000", email.text)

    def test_match_mail_shows_at_most_five_items(self) -> None:
        reservation = reservation_row()
        links = links_for(reservation)

        five = match_email("user@example.com", reservation, links, [item(i) for i in range(1, 6)])
        six = match_email("user@example.com", reservation, links, [item(i) for i in range(1, 7)])

        self.assertIn("에어팟 5", five.text)
        self.assertNotIn("그 밖에", five.text)
        self.assertIn("에어팟 5", six.text)
        self.assertNotIn("에어팟 6", six.text)
        self.assertIn("그 밖에 1개", six.text)

    def test_match_mail_offers_extension_only_while_extensions_remain(self) -> None:
        with_left = reservation_row(extension_count=1)
        used_up = reservation_row(extension_count=3)

        offered = match_email("user@example.com", with_left, links_for(with_left), [item(1)])
        not_offered = match_email("user@example.com", used_up, links_for(used_up), [item(1)])

        self.assertIn("남은 연장 2회", offered.text)
        self.assertIn("/alerts/extend", offered.text)
        self.assertNotIn("/alerts/extend", not_offered.text)

    def test_expiry_notice_offers_extension_or_a_new_reservation(self) -> None:
        with_left = reservation_row(extension_count=0)
        used_up = reservation_row(extension_count=3)

        offered = expiry_notice_email("user@example.com", with_left, links_for(with_left))
        not_offered = expiry_notice_email("user@example.com", used_up, links_for(used_up))

        self.assertEqual(offered.subject, EXPIRY_SUBJECT)
        self.assertIn("/alerts/extend", offered.text)
        self.assertIn("남은 연장은 3회", offered.text)
        self.assertNotIn("/alerts/extend", not_offered.text)
        self.assertIn("새로 예약", not_offered.text)

    def test_user_input_is_escaped_in_html_and_kept_out_of_the_subject(self) -> None:
        reservation = reservation_row(query="<script>alert(1)</script>", place_name="서울역 <b>")

        email = match_email("user@example.com", reservation, links_for(reservation), [item(1)])

        self.assertNotIn("<script>", email.html)
        self.assertIn("&lt;script&gt;", email.html)
        self.assertNotIn("<b>", email.html)
        self.assertNotIn("script", email.subject)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `uv run --with 'pytest>=8,<9' pytest tests/alerts/test_alert_templates.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'retriever_lost_found.alerts.templates'`

- [ ] **Step 3: 구현 쓰기**

`src/retriever_lost_found/alerts/templates.py`:

```python
from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any

from .links import KOREA_TIMEZONE, MAX_EXTENSIONS, MailLinks, parse_timestamp
from .sender import OutgoingEmail

MAX_ITEMS_PER_MAIL = 5  # 설계 2장 — 메일 한 통에 물품 최대 5개
# 설계 7장 — 제목에는 사용자 입력을 넣지 않는다(헤더 주입 방지).
CONFIRM_SUBJECT = "[리트리버] 분실물 알림 예약을 확인해 주세요"
MATCH_SUBJECT = "[리트리버] 저장한 조건과 유사한 물품이 새로 등록됐습니다"
EXPIRY_SUBJECT = "[리트리버] 분실물 알림 예약이 내일 끝납니다"
# AGENTS.md 2장 10번 부연 — "유사한 물품"으로 쓰고 본인 물건이라고 단정하지 않는다.
MATCH_HEADLINE = "저장한 조건과 유사한 물품이 새로 등록됐습니다."
OWNERSHIP_NOTICE = "본인 물건인지는 보관기관에 직접 확인해 주세요."
_WEEKDAYS = "월화수목금토일"


def format_expiry(value: str | datetime) -> str:
    local = parse_timestamp(value).astimezone(KOREA_TIMEZONE)
    period = "오전" if local.hour < 12 else "오후"
    hour = local.hour if local.hour <= 12 else local.hour - 12
    return f"{local.month}월 {local.day}일({_WEEKDAYS[local.weekday()]}) {period} {hour}시"


def _extensions_left(reservation: dict[str, Any]) -> int:
    return MAX_EXTENSIONS - int(reservation["extension_count"])


def _condition_lines(reservation: dict[str, Any]) -> str:
    radius_km = int(reservation["radius_m"]) // 1000
    return "\n".join(
        [
            f"장소: {reservation['place_name']} 반경 {radius_km}km 안 모든 보관기관",
            f"검색어: {reservation['query']}",
            f"알림 기간: {format_expiry(reservation['expires_at'])}까지",
        ]
    )


def _item_line(item: dict[str, Any]) -> str:
    details = ", ".join(value for value in (item.get("raw_category_name"), item.get("color_name")) if value)
    location = " ".join(
        value for value in (item.get("storage_location_name"), item.get("storage_location_phone")) if value
    )
    name = item.get("item_name") or "이름 없는 물품"
    parts = [name + (f" ({details})" if details else ""), f"{item.get('registered_on')} 등록"]
    if location:
        parts.append(f"보관: {location}")
    return " · ".join(parts)


def _render(to: str, subject: str, paragraphs: list[str], actions: list[tuple[str, str]]) -> OutgoingEmail:
    """paragraphs에는 사용자 입력이 들어 있다 — HTML에서는 모두 escape한다(설계 7장)."""
    action_lines = "\n".join(f"{label}: {url}" for label, url in actions)
    html_parts = [f"<p>{escape(paragraph).replace(chr(10), '<br>')}</p>" for paragraph in paragraphs]
    html_parts += [f'<p><a href="{escape(url, quote=True)}">{escape(label)}</a></p>' for label, url in actions]
    return OutgoingEmail(
        to=to,
        subject=subject,
        text="\n\n".join([*paragraphs, action_lines]),
        html="<html><body>" + "".join(html_parts) + "</body></html>",
    )


def confirmation_email(to: str, reservation: dict[str, Any], links: MailLinks) -> OutgoingEmail:
    paragraphs = [
        "리트리버 분실물 알림 예약을 신청하셨습니다.",
        _condition_lines(reservation),
        "아래 '예약 확인'을 눌러야 알림이 켜집니다. 이 링크는 24시간 동안 유효합니다.",
        "직접 신청하지 않으셨다면 이 메일을 무시하세요. 확인하지 않은 예약은 자동으로 사라집니다.",
    ]
    actions = [("예약 확인", links.confirm), ("예약 취소", links.cancel)]
    return _render(to, CONFIRM_SUBJECT, paragraphs, [(label, url) for label, url in actions if url])


def match_email(
    to: str,
    reservation: dict[str, Any],
    links: MailLinks,
    items: list[dict[str, Any]],
) -> OutgoingEmail:
    shown = items[:MAX_ITEMS_PER_MAIL]
    lines = [f"{index}. {_item_line(item)}" for index, item in enumerate(shown, start=1)]
    hidden = len(items) - len(shown)
    if hidden > 0:
        lines.append(f"그 밖에 {hidden}개가 더 있습니다. 리트리버에서 같은 조건으로 검색해 확인해 주세요.")
    paragraphs = [f"{MATCH_HEADLINE} {OWNERSHIP_NOTICE}", _condition_lines(reservation), "\n".join(lines)]
    actions = [("리트리버에서 보기", links.home)]
    if links.extend:
        actions.append((f"알림 1주 연장 (남은 연장 {_extensions_left(reservation)}회)", links.extend))
    actions.append(("예약 취소", links.cancel))
    return _render(to, MATCH_SUBJECT, paragraphs, actions)


def expiry_notice_email(to: str, reservation: dict[str, Any], links: MailLinks) -> OutgoingEmail:
    paragraphs = [
        f"분실물 알림 예약이 {format_expiry(reservation['expires_at'])}에 끝납니다.",
        _condition_lines(reservation),
    ]
    actions: list[tuple[str, str]] = []
    if links.extend:
        paragraphs.append(
            f"더 받으려면 '알림 1주 연장'을 눌러 주세요. 남은 연장은 {_extensions_left(reservation)}회입니다."
        )
        actions.append(("알림 1주 연장", links.extend))
    else:
        paragraphs.append("연장할 수 있는 횟수를 모두 사용했습니다. 계속 받으려면 리트리버에서 새로 예약해 주세요.")
        actions.append(("리트리버에서 새로 예약하기", links.home))
    actions.append(("예약 취소", links.cancel))
    return _render(to, EXPIRY_SUBJECT, paragraphs, actions)
```

- [ ] **Step 4: 테스트가 통과하는지 확인**

Run: `uv run --with 'pytest>=8,<9' pytest tests/alerts -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add src/retriever_lost_found/alerts/templates.py tests/alerts/test_alert_templates.py
git commit -m "feat: 확인·알림·만료 안내 메일 본문 — PRD 4.2 P1, AGENTS 2장 10번" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Supabase 저장소

**Files:**
- Create: `src/retriever_lost_found/alerts/store.py`
- Test: `tests/alerts/test_alert_store.py`

**Interfaces:**
- Consumes: Task 1의 DB 함수·테이블, `config.read_env_value`, `config.read_optional_env_value`
- Produces: `AlertStore`(Protocol)와 그 구현 `SupabaseAlertStore(url, secret_key, *, timeout=20.0)`, `SupabaseAlertStore.from_environment()`. 메서드(모든 시각은 ISO 문자열로 돌려준다):
  - `create_reservation(*, email, query, place_name, latitude, longitude, radius_m, ip_hash, link_nonce, expires_at: datetime) -> dict`
  - `delete_reservation(reservation_id: int) -> None`
  - `get_reservation(reservation_id: int) -> dict | None` — 키 `id, email, query, place_name, radius_m, status, link_nonce, created_at, expires_at, extension_count`
  - `confirm_reservation(reservation_id) -> str`, `extend_reservation(reservation_id) -> dict`, `cancel_reservation(reservation_id) -> str`
  - `match_queue(item_source_code) -> list[dict]` — 키 `id, query, checked_until`
  - `candidate_items(reservation_id, item_source_code) -> dict` — `{"checked_until": str | None, "items": list[dict]}`
  - `record_match(reservation_id, item_source_code, checked_until: str | None, items: list[tuple[int, float]]) -> dict` — `{"items_queued": int, ...}`
  - `enqueue_expiry_notices() -> int`
  - `claim_messages(*, limit: int, daily_cap: int, message_id: int | None = None) -> list[dict]` — 키 `id, reservation_id, kind, status, attempts, ...`
  - `message_payload(message_id) -> dict | None` — `{"message", "reservation", "items"}`
  - `finish_message(message_id, status: str, error: str | None = None) -> None`
  - `delete_message(message_id) -> None`
  - `close_ended_reservations() -> dict` — `{"expired": int, "unconfirmed_closed": int, ...}`
  - `start_run(kind: str, item_source_code: str | None = None) -> int`, `finish_run(run_id: int, values: dict) -> None`
  - 속성 `secret_key: str` — 오류 문구에서 가리는 데 쓴다

- [ ] **Step 1: 실패하는 테스트 쓰기**

`tests/alerts/test_alert_store.py`:

```python
from __future__ import annotations

import io
import json
import unittest
import urllib.error
from datetime import datetime, timezone
from unittest.mock import patch

from retriever_lost_found.alerts.store import SupabaseAlertStore


class FakeResponse:
    def __init__(self, payload) -> None:
        self.body = b"" if payload is None else json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self) -> bytes:
        return self.body


URLOPEN = "retriever_lost_found.alerts.store.urllib.request.urlopen"


class SupabaseAlertStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = SupabaseAlertStore("https://example.supabase.co/", "server-secret")

    @patch(URLOPEN)
    def test_create_reservation_calls_the_server_only_rpc(self, urlopen) -> None:
        urlopen.return_value = FakeResponse({"status": "created", "reservation_id": 3, "message_id": 4})

        result = self.store.create_reservation(
            email="user@example.com",
            query="에어팟",
            place_name="서울역",
            latitude=37.55,
            longitude=126.97,
            radius_m=1000,
            ip_hash="hash",
            link_nonce="nonce",
            expires_at=datetime(2026, 11, 13, 2, tzinfo=timezone.utc),
        )

        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.supabase.co/rest/v1/rpc/create_alert_reservation")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer server-secret")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["p_expires_at"], "2026-11-13T02:00:00+00:00")
        self.assertEqual(body["p_radius_m"], 1000)
        self.assertEqual(result["reservation_id"], 3)

    @patch(URLOPEN)
    def test_get_reservation_reads_one_row_or_none(self, urlopen) -> None:
        urlopen.return_value = FakeResponse([{"id": 7, "status": "active"}])
        self.assertEqual(self.store.get_reservation(7)["status"], "active")
        self.assertIn("id=eq.7", urlopen.call_args.args[0].full_url)

        urlopen.return_value = FakeResponse([])
        self.assertIsNone(self.store.get_reservation(8))

    @patch(URLOPEN)
    def test_record_match_sends_item_ids_with_scores(self, urlopen) -> None:
        urlopen.return_value = FakeResponse({"items_queued": 2, "message_id": 9})

        result = self.store.record_match(7, "partner_api", "2026-11-06T06:00:00+00:00", [(1, 0.9), (2, 0.8)])

        body = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(body["p_items"], [{"found_item_id": 1, "score": 0.9}, {"found_item_id": 2, "score": 0.8}])
        self.assertEqual(result["items_queued"], 2)

    @patch(URLOPEN)
    def test_empty_response_body_is_not_an_error(self, urlopen) -> None:
        urlopen.return_value = FakeResponse(None)

        self.assertIsNone(self.store.finish_message(5, "sent"))

    @patch(URLOPEN)
    def test_http_error_hides_the_secret_key(self, urlopen) -> None:
        urlopen.side_effect = urllib.error.HTTPError(
            "https://example.supabase.co",
            500,
            "error",
            {},
            io.BytesIO(b'{"message": "bad key server-secret"}'),
        )

        with self.assertRaises(RuntimeError) as caught:
            self.store.claim_messages(limit=1, daily_cap=450)

        self.assertNotIn("server-secret", str(caught.exception))
        self.assertIn("[REDACTED]", str(caught.exception))

    def test_requires_url_and_secret(self) -> None:
        with self.assertRaises(ValueError):
            SupabaseAlertStore("", "")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `uv run --with 'pytest>=8,<9' pytest tests/alerts/test_alert_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'retriever_lost_found.alerts.store'`

- [ ] **Step 3: 구현 쓰기**

`src/retriever_lost_found/alerts/store.py`:

```python
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Protocol

from ..config import read_env_value, read_optional_env_value

RESERVATION_FIELDS = "id,email,query,place_name,radius_m,status,link_nonce,created_at,expires_at,extension_count"


class AlertStore(Protocol):
    """설계 4.6 — 알림 예약 저장소. SupabaseAlertStore와 테스트의 FakeAlertStore가 따른다."""

    secret_key: str

    def create_reservation(
        self,
        *,
        email: str,
        query: str,
        place_name: str,
        latitude: float,
        longitude: float,
        radius_m: int,
        ip_hash: str | None,
        link_nonce: str,
        expires_at: datetime,
    ) -> dict[str, Any]: ...

    def delete_reservation(self, reservation_id: int) -> None: ...

    def get_reservation(self, reservation_id: int) -> dict[str, Any] | None: ...

    def confirm_reservation(self, reservation_id: int) -> str: ...

    def extend_reservation(self, reservation_id: int) -> dict[str, Any]: ...

    def cancel_reservation(self, reservation_id: int) -> str: ...

    def match_queue(self, item_source_code: str) -> list[dict[str, Any]]: ...

    def candidate_items(self, reservation_id: int, item_source_code: str) -> dict[str, Any]: ...

    def record_match(
        self,
        reservation_id: int,
        item_source_code: str,
        checked_until: str | None,
        items: list[tuple[int, float]],
    ) -> dict[str, Any]: ...

    def enqueue_expiry_notices(self) -> int: ...

    def claim_messages(
        self, *, limit: int, daily_cap: int, message_id: int | None = None
    ) -> list[dict[str, Any]]: ...

    def message_payload(self, message_id: int) -> dict[str, Any] | None: ...

    def finish_message(self, message_id: int, status: str, error: str | None = None) -> None: ...

    def delete_message(self, message_id: int) -> None: ...

    def close_ended_reservations(self) -> dict[str, Any]: ...

    def start_run(self, kind: str, item_source_code: str | None = None) -> int: ...

    def finish_run(self, run_id: int, values: dict[str, Any]) -> None: ...


class SupabaseAlertStore:
    """서버 전용 secret key로 알림 테이블·DB 함수를 부른다(절대 규칙 4·6)."""

    def __init__(self, url: str, secret_key: str, *, timeout: float = 20.0) -> None:
        self.url = url.rstrip("/")
        self.secret_key = secret_key.strip()
        self.timeout = timeout
        if not self.url or not self.secret_key:
            raise ValueError("Supabase URL과 서버 전용 secret key가 필요합니다.")

    @classmethod
    def from_environment(cls) -> SupabaseAlertStore:
        secret_key = read_optional_env_value("SUPABASE_SECRET_KEY") or read_optional_env_value(
            "SUPABASE_SERVICE_ROLE_KEY"
        )
        if not secret_key:
            raise ValueError("SUPABASE_SECRET_KEY 또는 SUPABASE_SERVICE_ROLE_KEY가 설정되지 않았습니다.")
        return cls(read_env_value("SUPABASE_URL"), secret_key)

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        payload: Any = None,
        prefer: str | None = None,
    ) -> Any:
        url = f"{self.url}/rest/v1/{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "apikey": self.secret_key,
            "Authorization": f"Bearer {self.secret_key}",
            "Accept": "application/json",
            "User-Agent": "RetrieverLostFound/0.1",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        if prefer:
            headers["Prefer"] = prefer
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:240]
            detail = detail.replace(self.secret_key, "[REDACTED]")
            raise RuntimeError(f"Supabase 알림 API HTTP {error.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError) as error:
            reason = getattr(error, "reason", error)
            raise RuntimeError(f"Supabase 알림 API 연결 실패: {reason}") from None
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            raise RuntimeError("Supabase 알림 API 응답이 올바른 JSON이 아닙니다.") from None

    def _rpc(self, name: str, args: dict[str, Any]) -> Any:
        return self._request("POST", f"rpc/{name}", payload=args)

    def create_reservation(
        self,
        *,
        email: str,
        query: str,
        place_name: str,
        latitude: float,
        longitude: float,
        radius_m: int,
        ip_hash: str | None,
        link_nonce: str,
        expires_at: datetime,
    ) -> dict[str, Any]:
        return dict(
            self._rpc(
                "create_alert_reservation",
                {
                    "p_email": email,
                    "p_query": query,
                    "p_place_name": place_name,
                    "p_latitude": latitude,
                    "p_longitude": longitude,
                    "p_radius_m": radius_m,
                    "p_ip_hash": ip_hash,
                    "p_link_nonce": link_nonce,
                    "p_expires_at": expires_at.isoformat(),
                },
            )
        )

    def delete_reservation(self, reservation_id: int) -> None:
        self._request(
            "DELETE",
            "alert_reservations",
            query={"id": f"eq.{int(reservation_id)}"},
            prefer="return=minimal",
        )

    def get_reservation(self, reservation_id: int) -> dict[str, Any] | None:
        rows = self._request(
            "GET",
            "alert_reservations",
            query={"select": RESERVATION_FIELDS, "id": f"eq.{int(reservation_id)}"},
        )
        if not isinstance(rows, list):
            raise RuntimeError("Supabase 알림 API 응답 형식이 올바르지 않습니다.")
        return rows[0] if rows else None

    def confirm_reservation(self, reservation_id: int) -> str:
        return str(self._rpc("confirm_alert_reservation", {"p_reservation_id": int(reservation_id)}))

    def extend_reservation(self, reservation_id: int) -> dict[str, Any]:
        return dict(self._rpc("extend_alert_reservation", {"p_reservation_id": int(reservation_id)}))

    def cancel_reservation(self, reservation_id: int) -> str:
        return str(self._rpc("cancel_alert_reservation", {"p_reservation_id": int(reservation_id)}))

    def match_queue(self, item_source_code: str) -> list[dict[str, Any]]:
        return list(self._rpc("alert_match_queue", {"p_item_source_code": item_source_code}) or [])

    def candidate_items(self, reservation_id: int, item_source_code: str) -> dict[str, Any]:
        payload = self._rpc(
            "alert_candidate_items",
            {"p_reservation_id": int(reservation_id), "p_item_source_code": item_source_code},
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise RuntimeError("Supabase 알림 후보 응답 형식이 올바르지 않습니다.")
        return payload

    def record_match(
        self,
        reservation_id: int,
        item_source_code: str,
        checked_until: str | None,
        items: list[tuple[int, float]],
    ) -> dict[str, Any]:
        return dict(
            self._rpc(
                "record_alert_match",
                {
                    "p_reservation_id": int(reservation_id),
                    "p_item_source_code": item_source_code,
                    "p_checked_until": checked_until,
                    "p_items": [{"found_item_id": item_id, "score": score} for item_id, score in items],
                },
            )
        )

    def enqueue_expiry_notices(self) -> int:
        return int(self._rpc("enqueue_alert_expiry_notices", {}) or 0)

    def claim_messages(
        self, *, limit: int, daily_cap: int, message_id: int | None = None
    ) -> list[dict[str, Any]]:
        rows = self._rpc(
            "claim_alert_messages",
            {"p_limit": int(limit), "p_daily_cap": int(daily_cap), "p_message_id": message_id},
        )
        return list(rows or [])

    def message_payload(self, message_id: int) -> dict[str, Any] | None:
        payload = self._rpc("alert_message_payload", {"p_message_id": int(message_id)})
        return payload if isinstance(payload, dict) else None

    def finish_message(self, message_id: int, status: str, error: str | None = None) -> None:
        self._rpc(
            "finish_alert_message",
            {"p_message_id": int(message_id), "p_status": status, "p_error": error},
        )

    def delete_message(self, message_id: int) -> None:
        self._request(
            "DELETE",
            "alert_messages",
            query={"id": f"eq.{int(message_id)}"},
            prefer="return=minimal",
        )

    def close_ended_reservations(self) -> dict[str, Any]:
        return dict(self._rpc("close_ended_alert_reservations", {}) or {})

    def start_run(self, kind: str, item_source_code: str | None = None) -> int:
        rows = self._request(
            "POST",
            "alert_runs",
            payload={"kind": kind, "item_source_code": item_source_code},
            prefer="return=representation",
        )
        if not isinstance(rows, list) or not rows:
            raise RuntimeError("알림 실행 기록을 만들지 못했습니다.")
        return int(rows[0]["id"])

    def finish_run(self, run_id: int, values: dict[str, Any]) -> None:
        self._request(
            "PATCH",
            "alert_runs",
            query={"id": f"eq.{int(run_id)}"},
            payload={**values, "finished_at": datetime.now(timezone.utc).isoformat()},
            prefer="return=minimal",
        )
```

- [ ] **Step 4: 테스트가 통과하는지 확인**

Run: `uv run --with 'pytest>=8,<9' pytest tests/alerts -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add src/retriever_lost_found/alerts/store.py tests/alerts/test_alert_store.py
git commit -m "feat: 알림 예약 Supabase 저장소 — PRD 4.2 P1, PRD 5 보안" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: 가짜 저장소와 예약 서비스(생성·요약·확인·연장·취소)

**Files:**
- Modify: `tests/alerts/alert_fakes.py` (끝에 `FakeAlertStore`, `FakeMonotonic` 추가)
- Create: `src/retriever_lost_found/alerts/reservations.py`
- Test: `tests/alerts/test_alert_reservations.py`

**Interfaces:**
- Consumes: `store.AlertStore`, `sender.EmailSender`·`SendError`, `settings.AlertSettings`, `links.*`, `templates.confirmation_email`·`format_expiry`, `errors.AlertLimitError`
- Produces:
  - `reservations.utc_now() -> datetime`, `reservations.normalize_email(value) -> str`
  - `reservations.AlertService(store, sender, settings, clock=utc_now)`:
    - `create(payload: dict, client_ip: str) -> dict` `{"status": "pending", "message": ...}` — 입력 오류 `ValueError`, 제한 `AlertLimitError`, 발송 실패 `RuntimeError`
    - `summary(token) -> dict` `{"status", "place_name", "radius_m", "query", "expires_at"(KST ISO), "extensions_left"}`
    - `confirm(token) -> dict` `{"result": confirmed|already_active|link_expired|ended, "message"}`
    - `extend(token) -> dict` `{"result": extended|limit_reached|ended, "message", ["expires_at", "extensions_left"]}`
    - `cancel(token) -> dict` `{"result": cancelled|already_ended, "message"}`
    - 토큰이 틀리면 `ValueError("링크가 올바르지 않습니다.")`
  - 테스트용 `FakeAlertStore(now=NOW)` — `AlertStore` 메서드 전부 + 준비용 `advance(**delta)`, `add_location(...)`, `add_found_item(...)`, `add_active_reservation(...) -> int`, `add_pending_match(reservation_id) -> int`, `queue_match(reservation_id, item_ids, *, score=1.0) -> int`, `add_sent_messages(count, *, sent_at)`. 내부 상태 `reservations`, `cursors`, `messages`, `notified`, `locations`, `found_items`, `runs`(시각은 `datetime`)
  - 테스트용 `FakeMonotonic(start=0.0, step=0.0)` — 시간 예산 테스트용 시계

- [ ] **Step 1: 가짜 저장소 만들기**

`tests/alerts/alert_fakes.py` 끝에 이어 붙인다. Task 1의 SQL 함수와 같은 규칙을 메모리에서 흉내 낸다.

```python
import itertools  # noqa: E402
import math  # noqa: E402
from datetime import timedelta  # noqa: E402

from retriever_lost_found.alerts.links import (  # noqa: E402
    EXTENSION_DAYS,
    MAX_EXTENSIONS,
    expires_at_for,
)

SOURCES = ("partner_api", "police_api")
EARTH_RADIUS_METERS = 6371008.7714  # PostGIS 구면 계산 반지름
PUBLIC_RESERVATION_FIELDS = (
    "id", "email", "query", "place_name", "radius_m", "status",
    "link_nonce", "created_at", "expires_at", "extension_count",
)
KIND_ORDER = {"confirm": 0, "match": 1, "expiry_notice": 2}


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


def _parse(value):
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def haversine_meters(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_METERS * math.asin(math.sqrt(a))


class FakeMonotonic:
    """시간 예산 테스트용 시계. 부를 때마다 step만큼 흐른다."""

    def __init__(self, start: float = 0.0, step: float = 0.0) -> None:
        self.value = start
        self.step = step

    def __call__(self) -> float:
        current = self.value
        self.value += self.step
        return current


class FakeAlertStore:
    """마이그레이션 20261106120000의 DB 함수와 같은 규칙을 메모리에서 흉내 낸다."""

    secret_key = "fake-supabase-secret"

    def __init__(self, now: datetime | None = None) -> None:
        self.now = now or NOW
        self._ids = itertools.count(1)
        self.reservations: dict[int, dict[str, Any]] = {}
        self.cursors: dict[tuple[int, str], datetime] = {}
        self.messages: dict[int, dict[str, Any]] = {}
        self.notified: dict[tuple[int, int], dict[str, Any]] = {}
        self.locations: dict[int, dict[str, Any]] = {}
        self.found_items: dict[int, dict[str, Any]] = {}
        self.runs: dict[int, dict[str, Any]] = {}

    # ---- 테스트 준비용 ----
    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)

    def add_location(self, location_id, latitude, longitude, *, name="서울역 유실물센터", phone="02-000-0000", is_active=True):
        self.locations[location_id] = {
            "id": location_id, "latitude": latitude, "longitude": longitude,
            "name": name, "phone": phone, "is_active": is_active,
        }

    def add_found_item(
        self, item_id, *, storage_location_id, item_source_code="partner_api", item_name="에어팟",
        raw_category_name="무선이어폰", color_name="흰색", description="", registered_on="2026-11-06",
        first_seen_at=None,
    ):
        self.found_items[item_id] = {
            "id": item_id, "storage_location_id": storage_location_id, "item_source_code": item_source_code,
            "item_name": item_name, "raw_category_name": raw_category_name, "color_name": color_name,
            "description": description, "normalized_search_text": item_name,
            "registered_on": registered_on, "first_seen_at": first_seen_at or self.now,
        }

    def add_active_reservation(
        self, *, email="user@example.com", query="에어팟", latitude=37.5547, longitude=126.9707,
        radius_m=1000, expires_at=None, extension_count=0, link_nonce="nonce-1",
    ) -> int:
        reservation_id = next(self._ids)
        self.reservations[reservation_id] = {
            "id": reservation_id, "email": email, "query": query, "place_name": "서울역",
            "latitude": latitude, "longitude": longitude, "radius_m": radius_m, "status": "active",
            "link_nonce": link_nonce, "ip_hash": "ip-hash", "created_at": self.now,
            "confirmed_at": self.now, "expires_at": expires_at or expires_at_for(self.now),
            "extension_count": extension_count, "ended_at": None,
        }
        for source in SOURCES:
            self.cursors[(reservation_id, source)] = self.now
        return reservation_id

    def add_pending_match(self, reservation_id: int) -> int:
        return self._insert_message(reservation_id, "match")

    def queue_match(self, reservation_id: int, item_ids, *, score: float = 1.0) -> int:
        self.record_match(reservation_id, "partner_api", None, [(item_id, score) for item_id in item_ids])
        return next(
            message["id"] for message in self.messages.values()
            if message["reservation_id"] == reservation_id and message["kind"] == "match"
            and message["status"] == "pending"
        )

    def add_sent_messages(self, count: int, *, sent_at: datetime) -> None:
        for _ in range(count):
            message_id = self._insert_message(0, "confirm")
            self.messages[message_id].update(status="sent", sent_at=sent_at)

    # ---- 내부 ----
    def _insert_message(self, reservation_id, kind, expiry_for=None) -> int:
        message_id = next(self._ids)
        self.messages[message_id] = {
            "id": message_id, "reservation_id": reservation_id, "kind": kind, "status": "pending",
            "expiry_for": expiry_for, "attempts": 0, "claimed_at": None, "sent_at": None,
            "last_error": None, "created_at": self.now,
        }
        return message_id

    def _public_reservation(self, reservation):
        return {field: _iso(reservation[field]) for field in PUBLIC_RESERVATION_FIELDS}

    def _public_message(self, message):
        return {key: _iso(value) for key, value in message.items()}

    def _end(self, reservation, status):
        reservation.update(status=status, email=None, ip_hash=None, ended_at=self.now)
        for message_id in [
            message_id for message_id, message in self.messages.items()
            if message["reservation_id"] == reservation["id"] and message["status"] == "pending"
        ]:
            self.delete_message(message_id)

    # ---- AlertStore ----
    def create_reservation(self, *, email, query, place_name, latitude, longitude, radius_m, ip_hash, link_nonce, expires_at):
        open_count = sum(
            1 for reservation in self.reservations.values()
            if reservation["email"] == email and reservation["status"] in ("pending", "active")
        )
        if open_count >= 3:
            return {"status": "email_limit"}
        if ip_hash is not None and sum(
            1 for reservation in self.reservations.values()
            if reservation["ip_hash"] == ip_hash and reservation["created_at"] > self.now - timedelta(hours=24)
        ) >= 5:
            return {"status": "ip_limit"}
        reservation_id = next(self._ids)
        self.reservations[reservation_id] = {
            "id": reservation_id, "email": email, "query": query.strip(), "place_name": place_name.strip(),
            "latitude": latitude, "longitude": longitude, "radius_m": radius_m, "status": "pending",
            "link_nonce": link_nonce, "ip_hash": ip_hash, "created_at": self.now, "confirmed_at": None,
            "expires_at": expires_at, "extension_count": 0, "ended_at": None,
        }
        for source in SOURCES:
            self.cursors[(reservation_id, source)] = self.now
        message_id = self._insert_message(reservation_id, "confirm")
        return {"status": "created", "reservation_id": reservation_id, "message_id": message_id, "expires_at": _iso(expires_at)}

    def delete_reservation(self, reservation_id):
        self.reservations.pop(reservation_id, None)
        for key in [key for key in self.cursors if key[0] == reservation_id]:
            del self.cursors[key]
        for message_id in [mid for mid, message in self.messages.items() if message["reservation_id"] == reservation_id]:
            self.delete_message(message_id)

    def get_reservation(self, reservation_id):
        reservation = self.reservations.get(reservation_id)
        return None if reservation is None else self._public_reservation(reservation)

    def confirm_reservation(self, reservation_id):
        reservation = self.reservations.get(reservation_id)
        if reservation is None:
            return "not_found"
        if reservation["status"] == "active":
            return "already_active"
        if reservation["status"] != "pending":
            return "ended"
        if reservation["created_at"] <= self.now - timedelta(hours=24):
            return "link_expired"
        reservation.update(status="active", confirmed_at=self.now)
        return "confirmed"

    def extend_reservation(self, reservation_id):
        reservation = self.reservations.get(reservation_id)
        if reservation is None:
            return {"result": "not_found"}
        if reservation["status"] != "active" or reservation["expires_at"] <= self.now:
            return {"result": "ended"}
        if reservation["extension_count"] >= MAX_EXTENSIONS:
            return {"result": "limit_reached"}
        reservation["expires_at"] += timedelta(days=EXTENSION_DAYS)
        reservation["extension_count"] += 1
        return {
            "result": "extended",
            "expires_at": _iso(reservation["expires_at"]),
            "extensions_left": MAX_EXTENSIONS - reservation["extension_count"],
        }

    def cancel_reservation(self, reservation_id):
        reservation = self.reservations.get(reservation_id)
        if reservation is None:
            return "not_found"
        if reservation["status"] not in ("pending", "active"):
            return "already_ended"
        self._end(reservation, "cancelled")
        return "cancelled"

    def match_queue(self, item_source_code):
        rows = [
            (self.cursors[(reservation["id"], item_source_code)], reservation["id"], reservation)
            for reservation in self.reservations.values()
            if reservation["status"] == "active" and reservation["expires_at"] > self.now
        ]
        rows.sort(key=lambda row: (row[0], row[1]))
        return [{"id": row[2]["id"], "query": row[2]["query"], "checked_until": _iso(row[0])} for row in rows]

    def candidate_items(self, reservation_id, item_source_code):
        reservation = self.reservations.get(reservation_id)
        cursor = self.cursors.get((reservation_id, item_source_code))
        if (
            reservation is None or cursor is None or reservation["status"] != "active"
            or reservation["expires_at"] <= self.now
        ):
            return {"checked_until": None, "items": []}
        scope = {
            location_id for location_id, location in self.locations.items()
            if location["is_active"] and haversine_meters(
                reservation["latitude"], reservation["longitude"], location["latitude"], location["longitude"]
            ) <= reservation["radius_m"]
        }
        items = [
            dict(item) for item in self.found_items.values()
            if item["storage_location_id"] in scope
            and item["item_source_code"] == item_source_code
            and item["first_seen_at"] > cursor
            and (reservation_id, item["id"]) not in self.notified
        ]
        items.sort(key=lambda item: (item["first_seen_at"], item["id"]))
        checked_until = max((item["first_seen_at"] for item in items), default=cursor)
        for item in items:
            item["first_seen_at"] = _iso(item["first_seen_at"])
        return {"checked_until": _iso(checked_until), "items": items}

    def record_match(self, reservation_id, item_source_code, checked_until, items):
        fresh = [(item_id, score) for item_id, score in items if (reservation_id, item_id) not in self.notified]
        queued = 0
        message_id = None
        if fresh:
            pending = next(
                (message for message in self.messages.values()
                 if message["reservation_id"] == reservation_id and message["kind"] == "match"
                 and message["status"] == "pending"),
                None,
            )
            message_id = pending["id"] if pending else self._insert_message(reservation_id, "match")
            for item_id, score in fresh:
                self.notified[(reservation_id, item_id)] = {"message_id": message_id, "score": score}
                queued += 1
        key = (reservation_id, item_source_code)
        if checked_until is not None and key in self.cursors:
            self.cursors[key] = max(self.cursors[key], _parse(checked_until))
        return {"items_queued": queued, "message_id": message_id}

    def enqueue_expiry_notices(self):
        count = 0
        for reservation in self.reservations.values():
            if reservation["status"] != "active":
                continue
            if not (self.now < reservation["expires_at"] <= self.now + timedelta(hours=36)):
                continue
            if any(
                message["reservation_id"] == reservation["id"] and message["kind"] == "expiry_notice"
                and message["expiry_for"] == reservation["expires_at"]
                for message in self.messages.values()
            ):
                continue
            self._insert_message(reservation["id"], "expiry_notice", expiry_for=reservation["expires_at"])
            count += 1
        return count

    def claim_messages(self, *, limit, daily_cap, message_id=None):
        for message in self.messages.values():
            if message["status"] == "sending" and message["claimed_at"] < self.now - timedelta(minutes=10):
                message.update(status="pending", claimed_at=None)
        sent = sum(
            1 for message in self.messages.values()
            if message["status"] == "sent" and message["sent_at"] > self.now - timedelta(hours=24)
        )
        sending = sum(1 for message in self.messages.values() if message["status"] == "sending")
        remaining = daily_cap - sent - sending
        if remaining <= 0:
            return []
        candidates = sorted(
            (message for message in self.messages.values()
             if message["status"] == "pending" and (message_id is None or message["id"] == message_id)),
            key=lambda message: (KIND_ORDER[message["kind"]], message["created_at"], message["id"]),
        )
        claimed = []
        for message in candidates[: min(max(limit, 0), remaining)]:
            message.update(status="sending", claimed_at=self.now, attempts=message["attempts"] + 1)
            claimed.append(self._public_message(message))
        return claimed

    def message_payload(self, message_id):
        message = self.messages.get(message_id)
        if message is None:
            return None
        reservation = self.reservations[message["reservation_id"]]
        items = []
        for (_, item_id), note in self.notified.items():
            if note["message_id"] != message_id:
                continue
            item = self.found_items[item_id]
            location = self.locations.get(item["storage_location_id"], {})
            items.append({
                "id": item_id, "item_name": item["item_name"], "raw_category_name": item["raw_category_name"],
                "color_name": item["color_name"], "registered_on": item["registered_on"],
                "storage_location_name": location.get("name"), "storage_location_phone": location.get("phone"),
                "score": note["score"],
            })
        # SQL과 같은 순서: 점수 높은 순(NULL 마지막) → 최근 등록 순 → id 큰 순
        items.sort(key=lambda item: item["id"], reverse=True)
        items.sort(key=lambda item: item["registered_on"], reverse=True)
        items.sort(key=lambda item: item["score"] if item["score"] is not None else -1.0, reverse=True)
        return {
            "message": self._public_message(message),
            "reservation": self._public_reservation(reservation),
            "items": items,
        }

    def finish_message(self, message_id, status, error=None):
        message = self.messages.get(message_id)
        if message is None or message["status"] != "sending":
            return
        message.update(
            status=status,
            last_error=error,
            sent_at=self.now if status == "sent" else None,
            claimed_at=None if status == "pending" else message["claimed_at"],
        )

    def delete_message(self, message_id):
        self.messages.pop(message_id, None)
        for key in [key for key, note in self.notified.items() if note["message_id"] == message_id]:
            del self.notified[key]

    def close_ended_reservations(self):
        expired = unconfirmed = 0
        for reservation in self.reservations.values():
            if reservation["status"] == "active" and reservation["expires_at"] <= self.now:
                self._end(reservation, "expired")
                expired += 1
            elif reservation["status"] == "pending" and reservation["created_at"] <= self.now - timedelta(hours=24):
                self._end(reservation, "unconfirmed_expired")
                unconfirmed += 1
        return {"expired": expired, "unconfirmed_closed": unconfirmed}

    def start_run(self, kind, item_source_code=None):
        run_id = next(self._ids)
        self.runs[run_id] = {"id": run_id, "kind": kind, "item_source_code": item_source_code, "status": "running"}
        return run_id

    def finish_run(self, run_id, values):
        self.runs[run_id].update(values)
```

- [ ] **Step 2: 실패하는 테스트 쓰기**

`tests/alerts/test_alert_reservations.py`:

```python
from __future__ import annotations

import json
import unittest
from datetime import timedelta

from alert_fakes import NOW, TEST_SETTINGS, FakeAlertStore, FakeEmailSender
from retriever_lost_found.alerts.errors import AlertLimitError
from retriever_lost_found.alerts.links import link_token
from retriever_lost_found.alerts.reservations import AlertService
from retriever_lost_found.alerts.sender import TemporarySendError


def request_payload(**overrides):
    payload = {
        "email": "User@Example.com",
        "query": "에어팟",
        "latitude": 37.5547,
        "longitude": 126.9707,
        "radius_m": 3000,
        "place_name": "서울역",
        "consent": True,
    }
    payload.update(overrides)
    return payload


class AlertServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.store = FakeAlertStore(NOW)
        self.sender = FakeEmailSender()
        self.service = AlertService(
            store=self.store, sender=self.sender, settings=TEST_SETTINGS, clock=lambda: self.store.now
        )

    def create(self, ip: str = "203.0.113.7", **overrides):
        return self.service.create(request_payload(**overrides), ip)

    def only_reservation(self) -> dict:
        (reservation,) = self.store.reservations.values()
        return reservation

    def token_for(self, reservation: dict) -> str:
        return link_token(TEST_SETTINGS.secret, reservation["id"], reservation["link_nonce"])


class CreateReservationTests(AlertServiceTestCase):
    def test_valid_request_saves_a_pending_reservation_and_sends_one_confirmation(self) -> None:
        result = self.create()

        self.assertEqual(result["status"], "pending")
        reservation = self.only_reservation()
        self.assertEqual(reservation["status"], "pending")
        self.assertEqual(reservation["email"], "user@example.com")
        self.assertEqual([email.to for email in self.sender.sent], ["user@example.com"])
        (message,) = self.store.messages.values()
        self.assertEqual((message["kind"], message["status"]), ("confirm", "sent"))

    def test_without_consent_nothing_is_saved_or_sent(self) -> None:
        with self.assertRaisesRegex(ValueError, "동의"):
            self.create(consent=False)

        self.assertEqual(len(self.store.reservations), 0)
        self.assertEqual(self.sender.sent, [])

    def test_query_must_be_one_to_thirty_characters(self) -> None:
        self.create(query="가" * 30)

        for query in ("가" * 31, "   ", None):
            with self.subTest(query=query), self.assertRaises(ValueError):
                self.create(query=query)

    def test_radius_must_be_one_of_the_screen_options(self) -> None:
        self.create(radius_m=3000)

        for radius in (2000, True, "3000", 3000.5):
            with self.subTest(radius=radius), self.assertRaises(ValueError):
                self.create(radius_m=radius)

    def test_coordinates_must_be_inside_korea(self) -> None:
        self.create()

        with self.assertRaisesRegex(ValueError, "대한민국"):
            self.create(latitude=40.7, longitude=-74.0)

    def test_email_is_validated(self) -> None:
        for email in ("no-at-sign", "two@@example.com", "a b@example.com", "user@localhost", "<x>@example.com", 7):
            with self.subTest(email=email), self.assertRaises(ValueError):
                self.create(email=email)

    def test_one_email_can_hold_three_open_reservations(self) -> None:
        for _ in range(3):
            self.create(email="User@Example.com")

        # 대소문자·공백만 바꿔도 같은 이메일로 센다.
        with self.assertRaises(AlertLimitError):
            self.create(email=" user@EXAMPLE.com ")
        self.assertEqual(len(self.store.reservations), 3)

    def test_one_ip_can_create_five_reservations_in_24_hours(self) -> None:
        for index in range(5):
            self.create(email=f"user{index}@example.com")

        with self.assertRaises(AlertLimitError):
            self.create(email="user5@example.com")
        self.create(email="user6@example.com", ip="198.51.100.1")  # 다른 IP는 된다
        self.store.advance(hours=25)
        self.create(email="user7@example.com")  # 24시간이 지나면 다시 된다

    def test_send_failure_leaves_no_reservation(self) -> None:
        self.sender.failures["user@example.com"] = [TemporarySendError("SMTP 421")]

        with self.assertRaisesRegex(RuntimeError, "확인 메일을 보내지 못했습니다"):
            self.create()

        self.assertEqual(len(self.store.reservations), 0)
        self.assertEqual(len(self.store.messages), 0)

    def test_daily_send_limit_blocks_new_reservations(self) -> None:
        self.store.add_sent_messages(450, sent_at=self.store.now - timedelta(hours=1))

        with self.assertRaises(AlertLimitError):
            self.create()

        self.assertEqual(len(self.store.reservations), 0)
        self.assertEqual(self.sender.sent, [])

    def test_link_token_is_mailed_but_never_stored(self) -> None:
        self.create()
        token = self.token_for(self.only_reservation())

        self.assertIn(token, self.sender.sent[0].text)
        self.assertNotIn(token, json.dumps(self.store.reservations, default=str))
        self.assertNotIn(token, json.dumps(self.store.messages, default=str))


class LinkActionTests(AlertServiceTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.create()
        self.reservation = self.only_reservation()
        self.token = self.token_for(self.reservation)

    def test_confirm_within_24_hours_turns_the_reservation_on(self) -> None:
        self.store.advance(hours=23)

        self.assertEqual(self.service.confirm(self.token)["result"], "confirmed")
        self.assertEqual(self.reservation["status"], "active")
        self.assertEqual(self.service.confirm(self.token)["result"], "already_active")

    def test_confirm_after_24_hours_is_refused(self) -> None:
        self.store.advance(hours=24, minutes=1)

        self.assertEqual(self.service.confirm(self.token)["result"], "link_expired")
        self.assertEqual(self.reservation["status"], "pending")

    def test_extend_adds_seven_days_three_times_only(self) -> None:
        self.service.confirm(self.token)
        first_expiry = self.reservation["expires_at"]

        for left in (2, 1, 0):
            outcome = self.service.extend(self.token)
            self.assertEqual(outcome["result"], "extended")
            self.assertEqual(outcome["extensions_left"], left)

        self.assertEqual(self.reservation["expires_at"], first_expiry + timedelta(days=21))
        self.assertEqual(self.service.extend(self.token)["result"], "limit_reached")

    def test_extend_after_expiry_or_before_confirmation_is_refused(self) -> None:
        self.assertEqual(self.service.extend(self.token)["result"], "ended")

        self.service.confirm(self.token)
        self.store.now = self.reservation["expires_at"] + timedelta(minutes=1)
        self.assertEqual(self.service.extend(self.token)["result"], "ended")

    def test_cancel_deletes_the_email_and_the_waiting_mail(self) -> None:
        self.service.confirm(self.token)
        self.store.add_pending_match(self.reservation["id"])

        self.assertEqual(self.service.cancel(self.token)["result"], "cancelled")

        self.assertEqual(self.reservation["status"], "cancelled")
        self.assertIsNone(self.reservation["email"])
        self.assertEqual([m for m in self.store.messages.values() if m["status"] == "pending"], [])
        self.assertEqual(self.service.cancel(self.token)["result"], "already_ended")

    def test_wrong_token_is_a_bad_link(self) -> None:
        wrong_secret = link_token("other-secret", self.reservation["id"], self.reservation["link_nonce"])
        unknown = f"{self.reservation['id'] + 100}.{'0' * 32}"

        for token in (wrong_secret, unknown, "garbage", None):
            with self.subTest(token=token), self.assertRaisesRegex(ValueError, "링크가 올바르지 않습니다"):
                self.service.confirm(token)

    def test_summary_reads_without_changing_anything(self) -> None:
        before = dict(self.reservation)

        summary = self.service.summary(self.token)

        self.assertEqual(summary["status"], "pending")
        self.assertEqual(summary["radius_m"], 3000)
        self.assertEqual(summary["extensions_left"], 3)
        self.assertEqual(summary["expires_at"], "2026-11-13T11:00:00+09:00")
        self.assertEqual(self.reservation, before)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 테스트가 실패하는지 확인**

Run: `uv run --with 'pytest>=8,<9' pytest tests/alerts/test_alert_reservations.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'retriever_lost_found.alerts.reservations'`

- [ ] **Step 4: 구현 쓰기**

`src/retriever_lost_found/alerts/reservations.py`:

```python
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .errors import AlertLimitError
from .links import (
    KOREA_TIMEZONE,
    MAX_EXTENSIONS,
    expires_at_for,
    ip_hash,
    mail_links,
    new_link_nonce,
    parse_timestamp,
    token_matches,
    token_reservation_id,
)
from .sender import EmailSender, SendError
from .settings import AlertSettings
from .store import AlertStore
from .templates import confirmation_email, format_expiry

ALLOWED_RADII_METERS = (1000, 3000, 5000)  # PRD 3.1-3 · 4.2 — 화면의 1·3·5km
MAX_QUERY_LENGTH = 30  # PRD 3.3 · D3 — 습득물 검색어 상한
MAX_PLACE_NAME_LENGTH = 100
MAX_EMAIL_LENGTH = 254
# 설계 14장 — 서비스 대상인 대한민국(독도 포함) 밖 좌표로는 예약하지 않는다.
LATITUDE_RANGE = (33.0, 38.7)
LONGITUDE_RANGE = (124.5, 132.0)
PENDING_MESSAGE = "확인 메일을 보냈습니다. 24시간 안에 링크를 눌러 주세요."
BAD_LINK = "링크가 올바르지 않습니다."
_FORBIDDEN_EMAIL_CHARACTERS = set('<>,;:"()[]\\')


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_email(value: Any) -> str:
    """설계 5.4 — 앞뒤 공백을 지우고 소문자로 저장해 이메일 제한을 대소문자로 피하지 못하게 한다."""
    if not isinstance(value, str):
        raise ValueError("이메일 형식이 올바르지 않습니다.")
    email = value.strip().lower()
    local, _, domain = email.partition("@")
    if (
        not email
        or len(email) > MAX_EMAIL_LENGTH
        or email.count("@") != 1
        or not local
        or "." not in domain
        or domain.startswith(".")
        or domain.endswith(".")
        or any(character.isspace() for character in email)
        or any(character in _FORBIDDEN_EMAIL_CHARACTERS for character in email)
    ):
        raise ValueError("이메일 형식이 올바르지 않습니다.")
    return email


def _query(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("검색어를 입력해 주세요.")
    query = value.strip()
    if len(query) > MAX_QUERY_LENGTH:
        raise ValueError("습득물 검색어는 30자 이하여야 합니다.")
    return query


def _place_name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("장소 이름이 없습니다.")
    place_name = value.strip()
    if len(place_name) > MAX_PLACE_NAME_LENGTH:
        raise ValueError("장소 이름은 100자 이하여야 합니다.")
    return place_name


def _coordinate(value: Any, name: str, bounds: tuple[float, float]) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} 좌표가 숫자가 아닙니다.")
    number = float(value)
    if not bounds[0] <= number <= bounds[1]:  # NaN도 여기서 걸린다
        raise ValueError("대한민국 안의 장소만 예약할 수 있습니다.")
    return number


def _radius(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value not in ALLOWED_RADII_METERS:
        raise ValueError("반경은 1km, 3km, 5km 중 하나여야 합니다.")
    return value


@dataclass
class AlertService:
    """설계 5.4 — 예약 API의 규칙. 저장·발송은 주입받는다(테스트는 가짜를 쓴다)."""

    store: AlertStore
    sender: EmailSender
    settings: AlertSettings
    clock: Callable[[], datetime] = utc_now

    def create(self, payload: dict[str, Any], client_ip: str) -> dict[str, str]:
        email = normalize_email(payload.get("email"))
        query = _query(payload.get("query"))
        place_name = _place_name(payload.get("place_name"))
        latitude = _coordinate(payload.get("latitude"), "위도", LATITUDE_RANGE)
        longitude = _coordinate(payload.get("longitude"), "경도", LONGITUDE_RANGE)
        radius_m = _radius(payload.get("radius_m"))
        if payload.get("consent") is not True:
            raise ValueError("개인정보 수집·이용에 동의해야 알림을 예약할 수 있습니다.")

        now = self.clock()
        created = self.store.create_reservation(
            email=email,
            query=query,
            place_name=place_name,
            latitude=latitude,
            longitude=longitude,
            radius_m=radius_m,
            ip_hash=ip_hash(self.settings.secret, client_ip),
            link_nonce=new_link_nonce(),
            expires_at=expires_at_for(now),
        )
        status = created.get("status")
        if status == "email_limit":
            raise AlertLimitError("같은 이메일로 진행 중인 알림 예약은 3개까지입니다.")
        if status == "ip_limit":
            raise AlertLimitError("같은 곳에서 하루에 만들 수 있는 알림 예약은 5개까지입니다. 내일 다시 시도해 주세요.")
        if status != "created":
            raise RuntimeError("알림 예약을 저장하지 못했습니다.")
        reservation_id = int(created["reservation_id"])
        message_id = int(created["message_id"])

        # 설계 2장 — 확인 메일도 하루 450통 안에서 센다.
        if not self.store.claim_messages(limit=1, daily_cap=self.settings.daily_send_limit, message_id=message_id):
            self.store.delete_reservation(reservation_id)
            raise AlertLimitError("오늘 보낼 수 있는 메일을 모두 보냈습니다. 내일 다시 시도해 주세요.")
        reservation = self.store.get_reservation(reservation_id)
        if reservation is None:
            raise RuntimeError("알림 예약을 저장하지 못했습니다.")
        message = confirmation_email(email, reservation, mail_links(self.settings, reservation, now))
        try:
            session = self.sender.open()
            try:
                session.send(message)
            finally:
                session.close()
        except SendError:
            # 설계 5.4 — 발송에 실패하면 예약을 남기지 않아 사용자가 바로 다시 시도할 수 있다.
            self.store.delete_reservation(reservation_id)
            raise RuntimeError("확인 메일을 보내지 못했습니다. 잠시 뒤 다시 시도해 주세요.") from None
        self.store.finish_message(message_id, "sent")
        return {"status": "pending", "message": PENDING_MESSAGE}

    def _reservation(self, token: Any) -> dict[str, Any]:
        if not isinstance(token, str):
            raise ValueError(BAD_LINK)
        reservation = self.store.get_reservation(token_reservation_id(token))
        if reservation is None or not token_matches(self.settings.secret, token, str(reservation["link_nonce"])):
            raise ValueError(BAD_LINK)
        return reservation

    def summary(self, token: Any) -> dict[str, Any]:
        """설계 5.4 — 링크 페이지의 요약. 상태를 바꾸지 않는다(링크를 열기만 해서는 처리하지 않는다)."""
        reservation = self._reservation(token)
        return {
            "status": reservation["status"],
            "place_name": reservation["place_name"],
            "radius_m": int(reservation["radius_m"]),
            "query": reservation["query"],
            "expires_at": parse_timestamp(reservation["expires_at"]).astimezone(KOREA_TIMEZONE).isoformat(),
            "extensions_left": MAX_EXTENSIONS - int(reservation["extension_count"]),
        }

    def confirm(self, token: Any) -> dict[str, str]:
        reservation = self._reservation(token)
        result = self.store.confirm_reservation(int(reservation["id"]))
        messages = {
            "confirmed": f"알림 예약이 켜졌습니다. {format_expiry(reservation['expires_at'])}까지 알려 드립니다.",
            "already_active": "이미 켜져 있는 예약입니다.",
            "link_expired": "확인 링크의 유효 시간(24시간)이 지났습니다. 다시 예약해 주세요.",
            "ended": "이미 끝난 예약입니다.",
        }
        if result not in messages:
            raise ValueError(BAD_LINK)
        return {"result": result, "message": messages[result]}

    def extend(self, token: Any) -> dict[str, Any]:
        reservation = self._reservation(token)
        outcome = self.store.extend_reservation(int(reservation["id"]))
        result = outcome.get("result")
        if result == "extended":
            expires_at = parse_timestamp(outcome["expires_at"]).astimezone(KOREA_TIMEZONE)
            left = int(outcome["extensions_left"])
            return {
                "result": "extended",
                "message": f"알림을 {format_expiry(expires_at)}까지 연장했습니다. 남은 연장은 {left}회입니다.",
                "expires_at": expires_at.isoformat(),
                "extensions_left": left,
            }
        if result == "limit_reached":
            return {"result": result, "message": "연장할 수 있는 횟수(3회)를 모두 사용했습니다."}
        if result == "ended":
            return {"result": result, "message": "끝났거나 아직 확인하지 않은 예약은 연장할 수 없습니다."}
        raise ValueError(BAD_LINK)

    def cancel(self, token: Any) -> dict[str, str]:
        reservation = self._reservation(token)
        result = self.store.cancel_reservation(int(reservation["id"]))
        if result == "cancelled":
            return {"result": result, "message": "알림 예약을 취소했습니다. 이메일 주소도 지웠습니다."}
        if result == "already_ended":
            return {"result": result, "message": "이미 끝난 예약입니다."}
        raise ValueError(BAD_LINK)
```

- [ ] **Step 5: 테스트가 통과하는지 확인**

Run: `uv run --with 'pytest>=8,<9' pytest tests/alerts -q`
Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add src/retriever_lost_found/alerts/reservations.py tests/alerts/alert_fakes.py tests/alerts/test_alert_reservations.py
git commit -m "feat: 알림 예약 생성·확인·연장·취소 규칙 — PRD 2.1-4, 4.2 P1" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: 예약 API 라우터와 앱 연결

**Files:**
- Create: `src/retriever_lost_found/web/alerts.py`
- Modify: `src/retriever_lost_found/web/app.py` (`create_app` 인자 추가, `create_runtime_app`에서 서비스 구성)
- Test: `tests/web/test_alert_api.py`

**Interfaces:**
- Consumes: `reservations.AlertService`, `errors.AlertLimitError`, `settings.load_alert_settings`, `store.SupabaseAlertStore`, `sender.SmtpEmailSender`
- Produces:
  - `web.alerts.client_ip(request) -> str`
  - `web.alerts.create_alert_router(service: AlertService | None) -> APIRouter` — `POST /api/alerts`(202), `GET /api/alerts/summary?token=`, `POST /api/alerts/confirm|extend|cancel` `{"token"}`(200). 서비스가 없으면 모두 503
  - `create_app(..., alert_service: AlertService | None = None)`

- [ ] **Step 1: 실패하는 테스트 쓰기**

`tests/web/test_alert_api.py`:

```python
from __future__ import annotations

import sys
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.requests import Request

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "tests" / "alerts"))

from alert_fakes import NOW, TEST_SETTINGS, FakeAlertStore, FakeEmailSender  # noqa: E402
from retriever_lost_found.alerts.links import link_token  # noqa: E402
from retriever_lost_found.alerts.reservations import AlertService  # noqa: E402
from retriever_lost_found.alerts.sender import TemporarySendError  # noqa: E402
from retriever_lost_found.integrations.kakao import KakaoLocalClient  # noqa: E402
from retriever_lost_found.web.alerts import client_ip  # noqa: E402
from retriever_lost_found.web.app import create_app  # noqa: E402

PAYLOAD = {
    "email": "user@example.com",
    "query": "에어팟",
    "latitude": 37.5547,
    "longitude": 126.9707,
    "radius_m": 3000,
    "place_name": "서울역",
    "consent": True,
}


class AlertApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = FakeAlertStore(NOW)
        self.sender = FakeEmailSender()
        service = AlertService(
            store=self.store, sender=self.sender, settings=TEST_SETTINGS, clock=lambda: self.store.now
        )
        self.client = TestClient(
            create_app(KakaoLocalClient("test-rest-key"), "test-js-key", alert_service=service)
        )

    def post(self, payload=None):
        return self.client.post("/api/alerts", json=payload or PAYLOAD)

    def test_create_returns_202_and_is_not_cached(self) -> None:
        response = self.post()

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["status"], "pending")
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_validation_errors_are_korean_400(self) -> None:
        response = self.post({**PAYLOAD, "consent": False})

        self.assertEqual(response.status_code, 400)
        self.assertIn("동의", response.json()["error"])

    def test_broken_json_is_400(self) -> None:
        response = self.client.post(
            "/api/alerts", content=b"{not json", headers={"Content-Type": "application/json"}
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "요청 형식이 올바르지 않습니다.")

    def test_fourth_reservation_for_one_email_is_429(self) -> None:
        for _ in range(3):
            self.assertEqual(self.post().status_code, 202)

        response = self.post()

        self.assertEqual(response.status_code, 429)
        self.assertIn("3개", response.json()["error"])

    def test_mail_failure_is_502(self) -> None:
        self.sender.failures["user@example.com"] = [TemporarySendError("SMTP 421")]

        self.assertEqual(self.post().status_code, 502)

    def test_link_endpoints_work_with_the_mailed_token(self) -> None:
        self.post()
        (reservation,) = self.store.reservations.values()
        token = link_token(TEST_SETTINGS.secret, reservation["id"], reservation["link_nonce"])

        summary = self.client.get("/api/alerts/summary", params={"token": token})
        self.assertEqual(summary.status_code, 200)
        self.assertEqual(summary.json()["status"], "pending")
        self.assertEqual(self.client.post("/api/alerts/confirm", json={"token": token}).json()["result"], "confirmed")
        self.assertEqual(self.client.post("/api/alerts/extend", json={"token": token}).json()["result"], "extended")
        self.assertEqual(self.client.post("/api/alerts/cancel", json={"token": token}).json()["result"], "cancelled")

    def test_bad_token_is_400(self) -> None:
        response = self.client.post("/api/alerts/confirm", json={"token": "garbage"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "링크가 올바르지 않습니다.")


class AlertApiWithoutSettingsTests(unittest.TestCase):
    def test_every_alert_endpoint_is_503_when_mail_is_not_configured(self) -> None:
        client = TestClient(create_app(KakaoLocalClient("test-rest-key"), "test-js-key"))

        self.assertEqual(client.post("/api/alerts", json=PAYLOAD).status_code, 503)
        self.assertEqual(client.get("/api/alerts/summary", params={"token": "1." + "0" * 32}).status_code, 503)
        for action in ("confirm", "extend", "cancel"):
            self.assertEqual(client.post(f"/api/alerts/{action}", json={"token": "x"}).status_code, 503)


class ClientIpTests(unittest.TestCase):
    @staticmethod
    def request(headers: dict[str, str]) -> Request:
        return Request(
            {
                "type": "http",
                "headers": [(name.lower().encode(), value.encode()) for name, value in headers.items()],
                "client": ("10.0.0.1", 1234),
            }
        )

    def test_prefers_vercel_real_ip_then_forwarded_for_then_the_socket(self) -> None:
        self.assertEqual(
            client_ip(self.request({"x-real-ip": "203.0.113.7", "x-forwarded-for": "198.51.100.1"})),
            "203.0.113.7",
        )
        self.assertEqual(client_ip(self.request({"x-forwarded-for": "198.51.100.1, 10.0.0.2"})), "198.51.100.1")
        self.assertEqual(client_ip(self.request({})), "10.0.0.1")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `uv run --with 'pytest>=8,<9' pytest tests/web/test_alert_api.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'retriever_lost_found.web.alerts'`

- [ ] **Step 3: 라우터 쓰기**

`src/retriever_lost_found/web/alerts.py`:

```python
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ..alerts.errors import AlertLimitError
from ..alerts.reservations import AlertService

UNAVAILABLE = {"error": "분실물 알림 예약을 지금은 사용할 수 없습니다."}


def client_ip(request: Request) -> str:
    """설계 5.2 — Vercel이 넣어 주는 x-real-ip를 먼저 본다."""
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded.strip():
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


async def _json_object(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except ValueError:
        raise ValueError("요청 형식이 올바르지 않습니다.") from None
    if not isinstance(payload, dict):
        raise ValueError("요청 형식이 올바르지 않습니다.")
    return payload


def create_alert_router(service: AlertService | None) -> APIRouter:
    """설계 5.4 — 예약 API. 오류는 앱의 ValueError(400)·RuntimeError(502) 처리기를 따른다."""
    router = APIRouter(prefix="/api/alerts", include_in_schema=False)

    async def run_token_action(request: Request, action: Callable[[Any], dict[str, Any]]) -> JSONResponse:
        payload = await _json_object(request)
        return JSONResponse(content=await run_in_threadpool(action, payload.get("token")))

    @router.post("")
    async def create_reservation(request: Request) -> JSONResponse:
        if service is None:
            return JSONResponse(status_code=503, content=UNAVAILABLE)
        payload = await _json_object(request)
        try:
            result = await run_in_threadpool(service.create, payload, client_ip(request))
        except AlertLimitError as error:
            return JSONResponse(status_code=429, content={"error": str(error)})
        return JSONResponse(status_code=202, content=result)

    @router.get("/summary")
    async def summary(token: str = "") -> JSONResponse:
        if service is None:
            return JSONResponse(status_code=503, content=UNAVAILABLE)
        return JSONResponse(content=await run_in_threadpool(service.summary, token))

    @router.post("/confirm")
    async def confirm(request: Request) -> JSONResponse:
        if service is None:
            return JSONResponse(status_code=503, content=UNAVAILABLE)
        return await run_token_action(request, service.confirm)

    @router.post("/extend")
    async def extend(request: Request) -> JSONResponse:
        if service is None:
            return JSONResponse(status_code=503, content=UNAVAILABLE)
        return await run_token_action(request, service.extend)

    @router.post("/cancel")
    async def cancel(request: Request) -> JSONResponse:
        if service is None:
            return JSONResponse(status_code=503, content=UNAVAILABLE)
        return await run_token_action(request, service.cancel)

    return router
```

- [ ] **Step 4: 앱에 연결하기**

`src/retriever_lost_found/web/app.py`를 고친다.

맨 위 import에 추가한다(`import re` 아래에 `import sys`, 기존 `from ..search.fuzzy import rank_found_items` 위에 나머지):

```python
import sys
```

```python
from ..alerts.reservations import AlertService
from ..alerts.sender import SmtpEmailSender
from ..alerts.settings import load_alert_settings
from ..alerts.store import SupabaseAlertStore
from .alerts import create_alert_router
```

`create_app` 인자 목록 끝(`found_item_client: SupabaseFoundItemClient | None = None,` 다음)에 추가한다.

```python
    alert_service: AlertService | None = None,
```

`create_app` 안의 `return app` 바로 위에 추가한다.

```python
    # 설계 5.4 — 메일 설정이 없으면 서비스가 None이고 /api/alerts*는 503이다.
    app.include_router(create_alert_router(alert_service))
```

`create_runtime_app`에서 `app = create_app(` 호출 바로 위에 추가하고, `create_app(...)` 호출 인자에 `alert_service=alert_service,`를 넣는다.

```python
    try:
        alert_settings = load_alert_settings()
    except ValueError as error:
        # 메일 설정이 잘못돼도 지도 서비스는 멈추지 않게 알림 예약만 끈다(설계 14장).
        print(f"[alerts] 알림 예약을 끕니다: {error}", file=sys.stderr)
        alert_settings = None
    alert_service = None
    if alert_settings is not None:
        alert_service = AlertService(
            store=SupabaseAlertStore(supabase_url, supabase_secret_key),
            sender=SmtpEmailSender(alert_settings.mail),
            settings=alert_settings,
        )
```

고친 뒤 `create_runtime_app`의 `create_app` 호출은 이렇게 된다.

```python
    app = create_app(
        KakaoLocalClient(rest_api_key),
        javascript_key,
        map_location_client=map_location_client,
        found_item_client=found_item_client,
        alert_service=alert_service,
    )
```

- [ ] **Step 5: 테스트가 통과하는지 확인**

Run: `uv run --with 'pytest>=8,<9' pytest tests/web tests/alerts -q`
Expected: PASS (기존 `tests/web` 테스트 포함)

- [ ] **Step 6: 커밋**

```bash
git add src/retriever_lost_found/web/alerts.py src/retriever_lost_found/web/app.py tests/web/test_alert_api.py
git commit -m "feat: 알림 예약 API(/api/alerts) — PRD 4.2 P1, 4.5 오류 응답" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: 매칭 단계와 수집 크론 연결

**Files:**
- Create: `src/retriever_lost_found/alerts/matching.py`
- Modify: `src/retriever_lost_found/web/cron.py` (`_run_source`에 매칭 연결)
- Test: `tests/alerts/test_alert_matching.py`, `tests/web/test_alert_cron.py`

**Interfaces:**
- Consumes: `store.AlertStore`(`start_run`, `match_queue`, `candidate_items`, `record_match`, `finish_run`), `errors.safe_error`, `ingestion.collector.SOURCE_CODES`
- Produces:
  - `matching.SimilarityJudge` — `(query: str, candidates: list[dict]) -> list[tuple[dict, float]]`
  - `matching.SIMILARITY_JUDGE: SimilarityJudge | None = None` — ⑥ 뒤 Task 11에서 채운다
  - `matching.MatchResult(items: list[tuple[dict, float]], checked_until: str | None)`
  - `matching.find_new_similar_items(store, judge, reservation: dict, item_source_code: str) -> MatchResult`
  - `matching.run_match_pass(store, judge, *, item_source_code: str, deadline: float, clock=time.monotonic) -> dict` `{"status", "reservations_checked", "reservations_matched", "items_queued", "error_message"}`
  - `web.cron.ALERT_DEADLINE_SECONDS = 260.0`

- [ ] **Step 1: 실패하는 매칭 테스트 쓰기**

`tests/alerts/test_alert_matching.py`:

```python
from __future__ import annotations

import unittest
from datetime import timedelta

from alert_fakes import NOW, FakeAlertStore, FakeMonotonic
from retriever_lost_found.alerts.matching import find_new_similar_items, run_match_pass


def keyword_judge(query, candidates):
    """⑥ 결론 전 테스트용 판정기 — 물품명에 검색어가 들어 있으면 유사로 본다."""
    return [(item, 1.0) for item in candidates if query in (item.get("item_name") or "")]


class MatchPassTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = FakeAlertStore(NOW)
        self.store.add_location(1, 37.5547 + 0.0089, 126.9707)  # 서울역 북쪽 약 990m — 1km 안
        self.store.add_location(2, 37.5547 + 0.0180, 126.9707)  # 약 2km — 1km 밖
        self.reservation_id = self.store.add_active_reservation(radius_m=1000)
        self.store.advance(hours=1)  # 이후 추가하는 물품은 예약 뒤에 들어온 것이다

    def run_pass(self, source: str = "partner_api", clock=None) -> dict:
        return run_match_pass(
            self.store,
            keyword_judge,
            item_source_code=source,
            deadline=100.0,
            clock=clock or FakeMonotonic(),
        )

    def queued_item_ids(self) -> list[int]:
        return sorted(item_id for (_, item_id) in self.store.notified)

    def test_new_item_inside_the_radius_is_queued_and_outside_is_not(self) -> None:
        self.store.add_found_item(10, storage_location_id=1)
        self.store.add_found_item(11, storage_location_id=2)

        result = self.run_pass()

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(self.queued_item_ids(), [10])

    def test_only_items_first_seen_after_the_reservation_count(self) -> None:
        self.store.add_found_item(10, storage_location_id=1, first_seen_at=NOW - timedelta(minutes=5))
        self.store.add_found_item(11, storage_location_id=1)

        self.run_pass()

        self.assertEqual(self.queued_item_ids(), [11])

    def test_unmatched_items_never_reach_the_mail(self) -> None:
        # 절대 규칙 5 — 기관 매칭에 실패한 물품(위치 NULL)은 지도처럼 알림에도 나오지 않는다.
        self.store.add_found_item(10, storage_location_id=None)
        self.store.add_found_item(11, storage_location_id=1)

        self.run_pass()

        self.assertEqual(self.queued_item_ids(), [11])

    def test_an_item_is_queued_only_once(self) -> None:
        self.store.add_found_item(10, storage_location_id=1)

        first = self.run_pass()
        second = self.run_pass()

        self.assertEqual((first["items_queued"], second["items_queued"]), (1, 0))
        self.assertEqual(len([m for m in self.store.messages.values() if m["kind"] == "match"]), 1)

    def test_partner_and_police_items_share_one_waiting_mail(self) -> None:
        self.store.add_found_item(10, storage_location_id=1, item_source_code="partner_api")
        self.store.add_found_item(11, storage_location_id=1, item_source_code="police_api")

        self.run_pass("partner_api")
        self.run_pass("police_api")

        matches = [m for m in self.store.messages.values() if m["kind"] == "match"]
        self.assertEqual(len(matches), 1)
        self.assertEqual({note["message_id"] for note in self.store.notified.values()}, {matches[0]["id"]})
        self.assertEqual(self.queued_item_ids(), [10, 11])

    def test_expired_reservation_gets_nothing(self) -> None:
        self.store.reservations[self.reservation_id]["expires_at"] = self.store.now - timedelta(minutes=1)
        self.store.add_found_item(10, storage_location_id=1)

        result = self.run_pass()

        self.assertEqual(result["reservations_checked"], 0)
        self.assertEqual(self.queued_item_ids(), [])

    def test_items_the_judge_rejects_are_not_looked_at_again(self) -> None:
        self.store.add_found_item(10, storage_location_id=1, item_name="지갑")

        self.run_pass()

        self.assertEqual(self.queued_item_ids(), [])
        self.assertEqual(self.store.candidate_items(self.reservation_id, "partner_api")["items"], [])

    def test_time_budget_stops_the_pass_and_the_next_run_catches_up(self) -> None:
        self.store.add_found_item(10, storage_location_id=1)

        late = self.run_pass(clock=FakeMonotonic(start=100.0))

        self.assertEqual(late["status"], "partial")
        self.assertEqual(self.queued_item_ids(), [])
        self.assertEqual(self.run_pass()["items_queued"], 1)

    def test_failures_are_recorded_without_the_secret(self) -> None:
        def broken_judge(query, candidates):
            raise RuntimeError(f"DB down {self.store.secret_key}")

        self.store.add_found_item(10, storage_location_id=1)

        result = run_match_pass(
            self.store, broken_judge, item_source_code="partner_api", deadline=100.0, clock=FakeMonotonic()
        )

        self.assertEqual(result["status"], "failed")
        (run,) = [run for run in self.store.runs.values() if run["kind"] == "match"]
        self.assertEqual(run["status"], "failed")
        self.assertNotIn(self.store.secret_key, run["error_message"])
        self.assertIn("[REDACTED]", run["error_message"])

    def test_judge_is_not_called_without_candidates(self) -> None:
        calls = []

        def judge(query, candidates):
            calls.append(candidates)
            return []

        result = find_new_similar_items(
            self.store, judge, {"id": self.reservation_id, "query": "에어팟"}, "partner_api"
        )

        self.assertEqual(calls, [])
        self.assertEqual(result.items, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 실패하는 크론 연결 테스트 쓰기**

`tests/web/test_alert_cron.py` (Task 9에서 클래스를 하나 더 붙인다):

```python
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 테스트가 실패하는지 확인**

Run: `uv run --with 'pytest>=8,<9' pytest tests/alerts/test_alert_matching.py tests/web/test_alert_cron.py -q`
Expected: FAIL — `retriever_lost_found.alerts.matching` 없음, `cron`에 `SupabaseAlertStore`·`run_match_pass` 없음

- [ ] **Step 4: 매칭 모듈 쓰기**

`src/retriever_lost_found/alerts/matching.py`:

```python
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .errors import safe_error
from .store import AlertStore


class SimilarityJudge(Protocol):
    def __call__(
        self, query: str, candidates: list[dict[str, Any]]
    ) -> list[tuple[dict[str, Any], float]]: ...


# 설계 3.2 — ⑥(검색 방식 확정) 결론으로 채운다(Task 11). None이면 매칭 단계를 돌리지 않는다.
SIMILARITY_JUDGE: SimilarityJudge | None = None


@dataclass(frozen=True)
class MatchResult:
    items: list[tuple[dict[str, Any], float]]
    checked_until: str | None


def find_new_similar_items(
    store: AlertStore,
    judge: SimilarityJudge,
    reservation: dict[str, Any],
    item_source_code: str,
) -> MatchResult:
    """설계 3.2 — ⑥과의 경계. 판정을 DB로 옮기게 되면 이 함수의 속만 바꾼다."""
    payload = store.candidate_items(int(reservation["id"]), item_source_code)
    candidates = payload.get("items") or []
    matched = judge(str(reservation["query"]), candidates) if candidates else []
    return MatchResult(items=list(matched), checked_until=payload.get("checked_until"))


def run_match_pass(
    store: AlertStore,
    judge: SimilarityJudge,
    *,
    item_source_code: str,
    deadline: float,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """설계 6.1 — 수집 크론 끝에서 이 출처의 새 물품만 활성 예약과 비교한다."""
    run_id = store.start_run("match", item_source_code)
    counts = {"reservations_checked": 0, "reservations_matched": 0, "items_queued": 0}
    status = "succeeded"
    error_message = None
    try:
        for reservation in store.match_queue(item_source_code):
            if clock() >= deadline:
                # 남은 예약은 커서가 오래된 순으로 다음 날 먼저 처리된다.
                status = "partial"
                break
            result = find_new_similar_items(store, judge, reservation, item_source_code)
            recorded = store.record_match(
                int(reservation["id"]),
                item_source_code,
                result.checked_until,
                [(int(item["id"]), float(score)) for item, score in result.items],
            )
            counts["reservations_checked"] += 1
            queued = int(recorded.get("items_queued") or 0)
            if queued:
                counts["reservations_matched"] += 1
                counts["items_queued"] += queued
    except Exception as error:  # 설계 6.1 — 실패는 alert_runs에만 남긴다
        status = "failed"
        error_message = safe_error(error, (store.secret_key,))
    store.finish_run(run_id, {"status": status, **counts, "error_message": error_message})
    return {"status": status, **counts, "error_message": error_message}
```

- [ ] **Step 5: 수집 크론에 연결하기**

`src/retriever_lost_found/web/cron.py`를 고친다. import 구역을 이렇게 바꾼다.

```python
from __future__ import annotations

import hmac
import sys
import time
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..alerts import matching
from ..alerts.matching import run_match_pass
from ..alerts.store import SupabaseAlertStore
from ..config import read_optional_env_value
from ..ingestion.collector import SOURCE_CODES
from ..ingestion.service import run_incremental_sync


router = APIRouter(prefix="/api/cron", include_in_schema=False)
# 설계 6.1·6.2 — 함수 300초 제한 안에서 기록·응답에 40초를 남긴다.
ALERT_DEADLINE_SECONDS = 260.0
```

`_authorized` 아래에 함수를 추가하고, `_run_source`를 이렇게 바꾼다(응답 부분은 그대로).

```python
def _run_alert_matching(source: str, result: dict[str, Any], *, deadline: float) -> None:
    """설계 6.1 — 알림 매칭. 실패해도 수집 응답과 ingestion_runs는 그대로다."""
    judge = matching.SIMILARITY_JUDGE
    source_result = result.get("source") if isinstance(result.get("source"), dict) else {}
    try:
        inserted = int(source_result.get("inserted") or 0)
    except (TypeError, ValueError):
        inserted = 0
    # 판정이 없거나, 저장까지 못 했거나, 새 물품이 없으면 DB를 부르지 않는다.
    if judge is None or result.get("status") not in ("succeeded", "partial") or inserted <= 0:
        return
    try:
        store = SupabaseAlertStore.from_environment()
        run_match_pass(store, judge, item_source_code=SOURCE_CODES[source], deadline=deadline)
    except Exception as error:
        print(f"[alerts] {source} 매칭 단계 실패: {type(error).__name__}", file=sys.stderr)


def _run_source(request: Request, source: Literal["partner", "police"]) -> JSONResponse:
    started = time.monotonic()
    authorization_error = _authorized(request)
    if authorization_error is not None:
        return authorization_error
    result = run_incremental_sync(source)
    _run_alert_matching(source, result, deadline=started + ALERT_DEADLINE_SECONDS)
    return JSONResponse(
        status_code=200 if result.get("status") == "succeeded" else 500,
        content=result,
        headers={"Cache-Control": "no-store"},
    )
```

- [ ] **Step 6: 테스트가 통과하는지 확인**

Run: `uv run --with 'pytest>=8,<9' pytest tests/alerts tests/web -q`
Expected: PASS — 기존 `tests/web/test_cron_endpoint.py`도 그대로 통과한다(그 결과에는 `inserted`가 없어 매칭을 건너뛴다)

- [ ] **Step 7: 커밋**

```bash
git add src/retriever_lost_found/alerts/matching.py src/retriever_lost_found/web/cron.py tests/alerts/test_alert_matching.py tests/web/test_alert_cron.py
git commit -m "feat: 수집 크론 끝 알림 매칭 단계(판정은 ⑥ 뒤) — PRD 4.2 P1, PRD 5 출처 독립" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: 발송 단계와 발송 크론

**Files:**
- Create: `src/retriever_lost_found/alerts/dispatch.py`
- Modify: `src/retriever_lost_found/web/cron.py` (`/api/cron/alert-send` 추가)
- Modify: `vercel.json`, `tests/deployment/test_vercel_config.py`
- Test: `tests/alerts/test_alert_dispatch.py`, `tests/web/test_alert_cron.py`(클래스 추가)

**Interfaces:**
- Consumes: `store.AlertStore`, `sender.*`, `settings.AlertSettings`, `links.mail_links`, `templates.*`, `reservations.utc_now`, `errors.safe_error`
- Produces:
  - `dispatch.SEND_BATCH_SIZE = 20`, `dispatch.MAX_SEND_ATTEMPTS = 3`
  - `dispatch.compose_message(store, settings, message: dict, now: datetime) -> OutgoingEmail | None`
  - `dispatch.run_send_pass(store, sender, settings, *, deadline: float, clock=time.monotonic, now=utc_now) -> dict` `{"status", "expiry_notices_queued", "messages_sent", "messages_deferred", "messages_failed", "reservations_expired", "reservations_unconfirmed_closed", "error_message"}`
  - `GET /api/cron/alert-send` — Bearer 필요, 설정 없으면 503, 실행 실패면 500

- [ ] **Step 1: 실패하는 발송 테스트 쓰기**

`tests/alerts/test_alert_dispatch.py`:

```python
from __future__ import annotations

import unittest
from datetime import timedelta

from alert_fakes import NOW, TEST_SETTINGS, FakeAlertStore, FakeEmailSender, FakeMonotonic
from retriever_lost_found.alerts.dispatch import run_send_pass
from retriever_lost_found.alerts.sender import (
    DailyLimitExceeded,
    LoginFailed,
    PermanentSendError,
    TemporarySendError,
)
from retriever_lost_found.alerts.templates import CONFIRM_SUBJECT, EXPIRY_SUBJECT, MATCH_SUBJECT


class SendPassTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = FakeAlertStore(NOW)
        self.store.add_location(1, 37.5547, 126.9707)
        self.sender = FakeEmailSender()
        self._next_item = 100

    def run_pass(self, clock=None) -> dict:
        return run_send_pass(
            self.store,
            self.sender,
            TEST_SETTINGS,
            deadline=100.0,
            clock=clock or FakeMonotonic(),
            now=lambda: self.store.now,
        )

    def reservation_with_alert(self, email: str, **reservation) -> tuple[int, int]:
        reservation_id = self.store.add_active_reservation(email=email, **reservation)
        self._next_item += 1
        self.store.add_found_item(self._next_item, storage_location_id=1)
        return reservation_id, self.store.queue_match(reservation_id, [self._next_item])

    def match_statuses(self) -> list[str]:
        return sorted(m["status"] for m in self.store.messages.values() if m["kind"] == "match")

    def new_pending_reservation(self, email: str) -> dict:
        return self.store.create_reservation(
            email=email, query="에어팟", place_name="서울역", latitude=37.5547, longitude=126.9707,
            radius_m=1000, ip_hash=None, link_nonce="nonce-new", expires_at=self.store.now + timedelta(days=7),
        )

    def test_sends_the_waiting_alert_mail(self) -> None:
        self.reservation_with_alert("a@example.com")

        result = self.run_pass()

        self.assertEqual((result["status"], result["messages_sent"]), ("succeeded", 1))
        self.assertEqual([email.subject for email in self.sender.sent], [MATCH_SUBJECT])
        self.assertEqual(self.match_statuses(), ["sent"])

    def test_daily_cap_counts_the_last_24_hours_only(self) -> None:
        self.store.add_sent_messages(449, sent_at=self.store.now - timedelta(hours=1))
        for index in range(3):
            self.reservation_with_alert(f"u{index}@example.com")

        self.assertEqual(self.run_pass()["messages_sent"], 1)
        self.assertEqual(self.match_statuses(), ["pending", "pending", "sent"])

        self.store.advance(hours=24)  # 449통이 24시간 밖으로 나간다
        self.assertEqual(self.run_pass()["messages_sent"], 2)

    def test_confirmation_then_alerts_then_expiry_notices(self) -> None:
        self.store.add_active_reservation(email="expiring@example.com", expires_at=self.store.now + timedelta(hours=24))
        self.reservation_with_alert("match@example.com")
        self.new_pending_reservation("new@example.com")

        self.run_pass()

        self.assertEqual([email.subject for email in self.sender.sent], [CONFIRM_SUBJECT, MATCH_SUBJECT, EXPIRY_SUBJECT])

    def test_permanent_failure_fails_one_mail_and_the_next_still_goes(self) -> None:
        self.reservation_with_alert("bad@example.com")
        self.reservation_with_alert("good@example.com")
        self.sender.failures["bad@example.com"] = [PermanentSendError("SMTP 550")]

        result = self.run_pass()

        self.assertEqual((result["messages_failed"], result["messages_sent"]), (1, 1))
        self.assertEqual([email.to for email in self.sender.sent], ["good@example.com"])

    def test_gmail_daily_limit_stops_everything_and_keeps_the_rest_waiting(self) -> None:
        for index in range(3):
            self.reservation_with_alert(f"u{index}@example.com")
        self.sender.failures["u1@example.com"] = [DailyLimitExceeded("SMTP 550 하루 발송 한도 초과")]

        result = self.run_pass()

        self.assertEqual((result["status"], result["messages_sent"]), ("partial", 1))
        self.assertEqual(self.match_statuses(), ["pending", "pending", "sent"])
        self.assertEqual([email.to for email in self.sender.sent], ["u0@example.com"])

    def test_temporary_failures_wait_for_the_next_run_and_fail_on_the_third_try(self) -> None:
        _, message_id = self.reservation_with_alert("slow@example.com")
        self.sender.failures["slow@example.com"] = [TemporarySendError("SMTP 421")] * 3

        for expected in ("pending", "pending", "failed"):
            self.run_pass()
            self.assertEqual(self.store.messages[message_id]["status"], expected)
        self.assertEqual(self.sender.sent, [])

    def test_mail_stuck_in_sending_is_retried_after_ten_minutes(self) -> None:
        _, message_id = self.reservation_with_alert("a@example.com")
        self.store.messages[message_id].update(status="sending", claimed_at=self.store.now - timedelta(minutes=5))

        self.assertEqual(self.run_pass()["messages_sent"], 0)
        self.store.advance(minutes=6)
        self.assertEqual(self.run_pass()["messages_sent"], 1)

    def test_mail_for_a_reservation_cancelled_meanwhile_is_dropped(self) -> None:
        reservation_id, message_id = self.reservation_with_alert("a@example.com")
        self.store.reservations[reservation_id].update(status="cancelled", email=None, ip_hash=None)

        self.assertEqual(self.run_pass()["messages_sent"], 0)
        self.assertNotIn(message_id, self.store.messages)
        self.assertEqual(self.sender.sent, [])

    def test_empty_alert_mail_is_dropped(self) -> None:
        reservation_id = self.store.add_active_reservation(email="a@example.com")
        message_id = self.store.add_pending_match(reservation_id)

        self.assertEqual(self.run_pass()["messages_sent"], 0)
        self.assertNotIn(message_id, self.store.messages)

    def test_expiry_notice_once_per_expiry_date(self) -> None:
        reservation_id = self.store.add_active_reservation(
            email="a@example.com", expires_at=self.store.now + timedelta(hours=24)
        )

        self.assertEqual(self.run_pass()["expiry_notices_queued"], 1)
        self.assertEqual(self.run_pass()["expiry_notices_queued"], 0)
        self.store.extend_reservation(reservation_id)  # 만료일 +7일
        self.store.advance(days=7)
        self.assertEqual(self.run_pass()["expiry_notices_queued"], 1)
        self.assertEqual([email.subject for email in self.sender.sent], [EXPIRY_SUBJECT, EXPIRY_SUBJECT])

    def test_no_expiry_notice_two_days_before(self) -> None:
        self.store.add_active_reservation(email="a@example.com", expires_at=self.store.now + timedelta(hours=48))

        self.assertEqual(self.run_pass()["expiry_notices_queued"], 0)

    def test_cleanup_removes_emails_but_keeps_rows(self) -> None:
        expired = self.store.add_active_reservation(
            email="old@example.com", expires_at=self.store.now - timedelta(minutes=30)
        )
        created = self.new_pending_reservation("unconfirmed@example.com")
        # 이 테스트는 정리만 본다 — 확인 메일은 이미 나간 것으로 둔다.
        self.store.messages[created["message_id"]].update(status="sent", sent_at=self.store.now)
        self.store.advance(hours=24, minutes=1)

        result = self.run_pass()

        self.assertEqual((result["reservations_expired"], result["reservations_unconfirmed_closed"]), (1, 1))
        self.assertEqual(self.store.reservations[expired]["status"], "expired")
        self.assertIsNone(self.store.reservations[expired]["email"])
        unconfirmed = self.store.reservations[created["reservation_id"]]
        self.assertEqual(unconfirmed["status"], "unconfirmed_expired")
        self.assertIsNone(unconfirmed["email"])

    def test_unconfirmed_reservation_younger_than_a_day_is_kept(self) -> None:
        created = self.new_pending_reservation("unconfirmed@example.com")
        self.store.messages[created["message_id"]].update(status="sent", sent_at=self.store.now)
        self.store.advance(hours=23)

        self.run_pass()

        self.assertEqual(self.store.reservations[created["reservation_id"]]["email"], "unconfirmed@example.com")

    def test_expiry_day_morning_matches_go_out_before_cleanup(self) -> None:
        # 11:00 만료, 발송 크론은 11:30에 돌았다 — 그날 아침 매칭분을 보낸 뒤 정리한다(설계 5.3).
        reservation_id, _ = self.reservation_with_alert(
            "last@example.com", expires_at=self.store.now - timedelta(minutes=30)
        )

        result = self.run_pass()

        self.assertEqual(result["messages_sent"], 1)
        self.assertEqual(self.store.reservations[reservation_id]["status"], "expired")
        self.assertNotIn("/alerts/extend", self.sender.sent[0].text)

    def test_time_budget_leaves_mail_waiting(self) -> None:
        self.reservation_with_alert("a@example.com")

        result = self.run_pass(clock=FakeMonotonic(start=100.0))

        self.assertEqual((result["status"], result["messages_sent"]), ("partial", 0))
        self.assertEqual(self.match_statuses(), ["pending"])

    def test_login_failure_is_recorded_without_the_password(self) -> None:
        self.reservation_with_alert("a@example.com")
        self.sender.open_error = LoginFailed(f"SMTP 로그인 실패 {TEST_SETTINGS.mail.password}")

        result = self.run_pass()

        self.assertEqual(result["status"], "failed")
        self.assertNotIn(TEST_SETTINGS.mail.password, result["error_message"])
        self.assertIn("[REDACTED]", result["error_message"])
        self.assertEqual(self.match_statuses(), ["pending"])
        (run,) = [run for run in self.store.runs.values() if run["kind"] == "send"]
        self.assertEqual(run["status"], "failed")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `uv run --with 'pytest>=8,<9' pytest tests/alerts/test_alert_dispatch.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'retriever_lost_found.alerts.dispatch'`

- [ ] **Step 3: 발송 모듈 쓰기**

`src/retriever_lost_found/alerts/dispatch.py`:

```python
from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from .errors import safe_error
from .links import mail_links
from .reservations import utc_now
from .sender import (
    DailyLimitExceeded,
    EmailSender,
    EmailSession,
    LoginFailed,
    OutgoingEmail,
    PermanentSendError,
    TemporarySendError,
)
from .settings import AlertSettings
from .store import AlertStore
from .templates import confirmation_email, expiry_notice_email, match_email

SEND_BATCH_SIZE = 20
MAX_SEND_ATTEMPTS = 3  # 설계 6.3 — 일시 오류는 세 번째 시도에서 실패로 둔다


def compose_message(
    store: AlertStore,
    settings: AlertSettings,
    message: dict[str, Any],
    now: datetime,
) -> OutgoingEmail | None:
    """보낼 수 없는 메일(예약이 끝남·물품 없음)은 None — 호출한 쪽이 지운다."""
    payload = store.message_payload(int(message["id"]))
    if payload is None:
        return None
    reservation = payload["reservation"]
    email = reservation.get("email")
    status = reservation.get("status")
    # 설계 6.2 — 보내기 직전 상태만 다시 본다. 만료 시각은 보지 않는다(만료일 아침 매칭분을 보낸다).
    if not email or status not in ("pending", "active"):
        return None
    links = mail_links(settings, reservation, now)
    kind = message["kind"]
    if kind == "confirm":
        return confirmation_email(email, reservation, links) if status == "pending" else None
    if status != "active":
        return None
    if kind == "match":
        items = payload.get("items") or []
        return match_email(email, reservation, links, items) if items else None
    if kind == "expiry_notice":
        return expiry_notice_email(email, reservation, links)
    return None


def _release(store: AlertStore, messages: list[dict[str, Any]], counts: dict[str, int]) -> None:
    for message in messages:
        store.finish_message(int(message["id"]), "pending")
        counts["messages_deferred"] += 1


def _send_pending(
    store: AlertStore,
    sender: EmailSender,
    settings: AlertSettings,
    counts: dict[str, int],
    *,
    deadline: float,
    clock: Callable[[], float],
    now: Callable[[], datetime],
) -> str:
    session: EmailSession | None = None
    # 일시 오류 메일은 이번 실행에서 다시 집지 않게 sending으로 붙잡아 두었다가 끝에 되돌린다.
    retry_later: list[tuple[dict[str, Any], str]] = []
    try:
        while True:
            if clock() >= deadline:
                return "partial"
            batch = store.claim_messages(limit=SEND_BATCH_SIZE, daily_cap=settings.daily_send_limit)
            if not batch:
                return "succeeded"
            for index, message in enumerate(batch):
                message_id = int(message["id"])
                if clock() >= deadline:
                    _release(store, batch[index:], counts)
                    return "partial"
                email = compose_message(store, settings, message, now())
                if email is None:
                    store.delete_message(message_id)
                    continue
                try:
                    if session is None:
                        session = sender.open()
                    session.send(email)
                except LoginFailed:
                    _release(store, batch[index:], counts)
                    raise
                except DailyLimitExceeded:
                    _release(store, batch[index:], counts)
                    return "partial"
                except PermanentSendError as error:
                    store.finish_message(message_id, "failed", str(error))
                    counts["messages_failed"] += 1
                except TemporarySendError as error:
                    retry_later.append((message, str(error)))
                    if session is not None:
                        session.close()
                        session = None
                else:
                    store.finish_message(message_id, "sent")
                    counts["messages_sent"] += 1
    finally:
        if session is not None:
            session.close()
        for message, error in retry_later:
            final = int(message["attempts"]) >= MAX_SEND_ATTEMPTS
            store.finish_message(int(message["id"]), "failed" if final else "pending", error)
            counts["messages_failed" if final else "messages_deferred"] += 1


def run_send_pass(
    store: AlertStore,
    sender: EmailSender,
    settings: AlertSettings,
    *,
    deadline: float,
    clock: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = utc_now,
) -> dict[str, Any]:
    """설계 6.2 — ① 만료 안내 넣기 → ② 발송(450통 한도) → ③ 정리."""
    run_id = store.start_run("send")
    counts = {
        "expiry_notices_queued": 0,
        "messages_sent": 0,
        "messages_deferred": 0,
        "messages_failed": 0,
        "reservations_expired": 0,
        "reservations_unconfirmed_closed": 0,
    }
    secrets = (settings.mail.password, settings.secret, store.secret_key)
    status = "succeeded"
    errors: list[str] = []
    try:
        counts["expiry_notices_queued"] = int(store.enqueue_expiry_notices())
        status = _send_pending(store, sender, settings, counts, deadline=deadline, clock=clock, now=now)
    except Exception as error:
        status = "failed"
        errors.append(safe_error(error, secrets))
    try:
        # 발송 뒤에 정리해야 만료일 아침 매칭분이 나간다(설계 5.3).
        closed = store.close_ended_reservations()
        counts["reservations_expired"] = int(closed.get("expired") or 0)
        counts["reservations_unconfirmed_closed"] = int(closed.get("unconfirmed_closed") or 0)
    except Exception as error:
        status = "failed"
        errors.append(safe_error(error, secrets))
    error_message = " | ".join(errors) or None
    store.finish_run(run_id, {"status": status, **counts, "error_message": error_message})
    return {"status": status, **counts, "error_message": error_message}
```

- [ ] **Step 4: 발송 테스트가 통과하는지 확인**

Run: `uv run --with 'pytest>=8,<9' pytest tests/alerts/test_alert_dispatch.py -q`
Expected: PASS

- [ ] **Step 5: 실패하는 발송 크론 테스트 쓰기**

`tests/web/test_alert_cron.py`의 `MatchingHookTests` 클래스 아래(`if __name__ == "__main__":` 위)에 추가한다.

```python
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

    def test_failed_send_pass_is_500(self) -> None:
        response, _, _ = self.call(AUTH, result={"status": "failed", "messages_sent": 0})

        self.assertEqual(response.status_code, 500)
```

`tests/deployment/test_vercel_config.py`의 `test_daily_sync_runs_at_nine_am_korea_time` 안 기대값을 바꾼다. **11-06 팀 결정으로 사양이 바뀐 것**이라 테스트를 사양에 맞추는 것이며, PR 본문에 이유를 적는다.

```python
        self.assertEqual(
            config["crons"],
            [
                {"path": "/api/cron/partner-sync", "schedule": "0 0 * * *"},
                {"path": "/api/cron/police-sync", "schedule": "0 0 * * *"},
                # 설계 3장 — 발송 전용 크론. Hobby 크론은 지정 시간대 안 아무 때나 돌므로
                # 09시대 수집과 겹치지 않게 KST 11시(UTC 02시)에 둔다(2026-11-06 팀 결정).
                {"path": "/api/cron/alert-send", "schedule": "0 2 * * *"},
            ],
        )
```

- [ ] **Step 6: 테스트가 실패하는지 확인**

Run: `uv run --with 'pytest>=8,<9' pytest tests/web/test_alert_cron.py tests/deployment -q`
Expected: FAIL — `/api/cron/alert-send`가 404, `vercel.json` 크론이 2개

- [ ] **Step 7: 발송 크론 쓰기**

`src/retriever_lost_found/web/cron.py`의 import에 추가한다.

```python
from ..alerts.dispatch import run_send_pass
from ..alerts.sender import SmtpEmailSender
from ..alerts.settings import load_alert_settings
```

파일 끝(`police_sync` 아래)에 추가한다.

```python
@router.get("/alert-send")
def alert_send(request: Request) -> JSONResponse:
    """설계 6.2 — 만료 안내 → 발송(450통 한도) → 정리. KST 11시대에 한 번 돈다."""
    started = time.monotonic()
    authorization_error = _authorized(request)
    if authorization_error is not None:
        return authorization_error
    no_store = {"Cache-Control": "no-store"}
    try:
        settings = load_alert_settings()
    except ValueError as error:
        return JSONResponse(status_code=503, content={"error": str(error)}, headers=no_store)
    if settings is None:
        return JSONResponse(status_code=503, content={"error": "알림 메일 설정이 없습니다."}, headers=no_store)
    result = run_send_pass(
        SupabaseAlertStore.from_environment(),
        SmtpEmailSender(settings.mail),
        settings,
        deadline=started + ALERT_DEADLINE_SECONDS,
    )
    return JSONResponse(
        status_code=500 if result.get("status") == "failed" else 200,
        content=result,
        headers=no_store,
    )
```

`vercel.json`의 `crons`를 이렇게 바꾼다.

```json
  "crons": [
    {
      "path": "/api/cron/partner-sync",
      "schedule": "0 0 * * *"
    },
    {
      "path": "/api/cron/police-sync",
      "schedule": "0 0 * * *"
    },
    {
      "path": "/api/cron/alert-send",
      "schedule": "0 2 * * *"
    }
  ]
```

- [ ] **Step 8: 테스트가 통과하는지 확인**

Run: `uv run --with 'pytest>=8,<9' pytest -q`
Expected: 전체 PASS

- [ ] **Step 9: 커밋**

```bash
git add src/retriever_lost_found/alerts/dispatch.py src/retriever_lost_found/web/cron.py vercel.json tests/alerts/test_alert_dispatch.py tests/web/test_alert_cron.py tests/deployment/test_vercel_config.py
git commit -m "feat: 알림 메일 발송 단계와 발송 전용 크론(KST 11시) — PRD 4.2 P1, PRD 5 보안" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: 발송 테스트 명령과 환경 변수·문서

**Files:**
- Create: `src/retriever_lost_found/alerts/send_test.py`
- Test: `tests/alerts/test_alert_send_test.py`, `tests/deployment/test_alert_env.py`
- Modify: `.env.example`, `.github/workflows/ci.yml`, `README.md`, `docs/PRD.md`, `AGENTS.md`, `docs/team-roles.md`

**Interfaces:**
- Consumes: `settings.load_alert_settings`, `settings.REQUIRED_NAMES`, `sender.SmtpEmailSender`, `reservations.normalize_email`
- Produces: `python -m retriever_lost_found.alerts.send_test --to <주소>` — 성공 0, 설정 없음·발송 실패 1

- [ ] **Step 1: 실패하는 테스트 쓰기**

`tests/alerts/test_alert_send_test.py`:

```python
from __future__ import annotations

import unittest
from unittest.mock import patch

from alert_fakes import TEST_SETTINGS, FakeEmailSender
from retriever_lost_found.alerts import send_test


class SendTestCommandTests(unittest.TestCase):
    def test_sends_one_mail_to_the_given_address(self) -> None:
        fake = FakeEmailSender()
        with (
            patch.object(send_test, "load_alert_settings", return_value=TEST_SETTINGS),
            patch.object(send_test, "SmtpEmailSender", return_value=fake),
        ):
            self.assertEqual(send_test.main(["--to", "Me@Example.com"]), 0)

        self.assertEqual([email.to for email in fake.sent], ["me@example.com"])

    def test_missing_settings_sends_nothing(self) -> None:
        with (
            patch.object(send_test, "load_alert_settings", return_value=None),
            patch.object(send_test, "SmtpEmailSender") as sender,
        ):
            self.assertEqual(send_test.main(["--to", "me@example.com"]), 1)

        sender.assert_not_called()


if __name__ == "__main__":
    unittest.main()
```

`tests/deployment/test_alert_env.py`:

```python
from __future__ import annotations

import unittest
from pathlib import Path

from retriever_lost_found.alerts.settings import REQUIRED_NAMES

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class AlertEnvironmentTests(unittest.TestCase):
    def test_env_example_and_ci_list_every_alert_setting(self) -> None:
        # AGENTS.md 6장 — 환경 변수를 추가하면 .env.example·README·CI env를 같이 갱신한다.
        env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
        ci = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

        for name in REQUIRED_NAMES:
            with self.subTest(name=name):
                self.assertIn(f"{name}=", env_example)
                self.assertIn(f"{name}:", ci)
                self.assertIn(f"`{name}`", readme)
        self.assertIn("ALERT_DAILY_SEND_LIMIT=", env_example)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 테스트가 실패하는지 확인**

Run: `uv run --with 'pytest>=8,<9' pytest tests/alerts/test_alert_send_test.py tests/deployment/test_alert_env.py -q`
Expected: FAIL — `send_test` 모듈 없음, `.env.example`에 `MAIL_SMTP_HOST=` 없음

- [ ] **Step 3: 발송 테스트 명령 쓰기**

`src/retriever_lost_found/alerts/send_test.py`:

```python
"""설계 10.3-2 — DB 없이 메일 한 통만 보내 Gmail 연결과 스팸함 분류를 확인한다.

실행: uv run python -m retriever_lost_found.alerts.send_test --to <받을 주소>
실제 메일이 나가므로 받는 주소를 확인받은 뒤 실행한다. 받는 주소는 어디에도 저장하지 않는다.
"""

from __future__ import annotations

import argparse

from .reservations import normalize_email
from .sender import OutgoingEmail, SendError, SmtpEmailSender
from .settings import load_alert_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="리트리버 알림 메일 발송 테스트(한 통)")
    parser.add_argument("--to", required=True, help="받을 주소")
    args = parser.parse_args(argv)

    settings = load_alert_settings()
    if settings is None:
        print("메일 설정(MAIL_*, PUBLIC_BASE_URL, ALERT_SECRET)이 .env에 없습니다.")
        return 1
    email = OutgoingEmail(
        to=normalize_email(args.to),
        subject="[리트리버] 알림 메일 발송 테스트",
        text="리트리버 알림 메일 발송 테스트입니다.\n받은편지함에 왔는지, 스팸함·프로모션함으로 갔는지 확인해 주세요.",
        html="<p>리트리버 알림 메일 발송 테스트입니다.</p><p>받은편지함에 왔는지, 스팸함·프로모션함으로 갔는지 확인해 주세요.</p>",
    )
    try:
        session = SmtpEmailSender(settings.mail).open()
        try:
            session.send(email)
        finally:
            session.close()
    except SendError as error:
        print(f"발송 실패: {error}")
        return 1
    print("발송했습니다. 받은편지함과 스팸함을 확인해 주세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 환경 변수 파일 고치기**

`.env.example` 끝에 추가한다(값은 비운다).

```
MAIL_SMTP_HOST=
MAIL_SMTP_PORT=
MAIL_SMTP_USERNAME=
MAIL_SMTP_PASSWORD=
MAIL_FROM=
PUBLIC_BASE_URL=
ALERT_SECRET=
ALERT_DAILY_SEND_LIMIT=
```

`.github/workflows/ci.yml`의 `jobs.tests.env`에 더미 값을 추가한다(테스트는 가짜 발송기를 써서 실제로 보내지 않는다).

```yaml
      MAIL_SMTP_HOST: smtp.example.com
      MAIL_SMTP_PORT: "465"
      MAIL_SMTP_USERNAME: sender@example.com
      MAIL_SMTP_PASSWORD: test-mail-password
      MAIL_FROM: "리트리버 <sender@example.com>"
      PUBLIC_BASE_URL: https://retriever.example
      ALERT_SECRET: test-alert-secret
```

- [ ] **Step 5: README에 절 추가하기**

`README.md`의 `## 일일 데이터 동기화` 절 끝(`## 프로젝트 구조` 바로 위)에 추가한다.

````markdown
## 분실물 알림 메일(스케줄러)

검색 조건과 이메일로 알림을 예약하면, 1주일 동안 매일 새로 들어온 습득물 중 유사한 물품을
메일로 알립니다(PRD 2.1-4, 4.2 P1). 설계는 `docs/alert-scheduler/`에 있습니다.

- 매칭: 수집 크론(`partner-sync`·`police-sync`)이 끝날 때 그 출처의 새 물품만 비교합니다.
  유사 판정은 ⑥ 검색 방식 결론 뒤에 연결합니다.
- 발송: `/api/cron/alert-send`가 매일 UTC 02:00, 한국시간 오전 11시대에 돕니다.
  최근 24시간 450통까지 보냅니다.
- 메일 설정이 하나라도 없으면 알림 예약 API(`/api/alerts*`)는 503을 돌려줍니다.
  Preview 환경에는 메일 설정을 넣지 않아 실제 메일이 나가지 않게 합니다.

서버 전용 환경 변수입니다. 브라우저로 전달하지 않습니다.

- `MAIL_SMTP_HOST` — `smtp.gmail.com`
- `MAIL_SMTP_PORT` — `465`(SSL) 또는 `587`(STARTTLS)
- `MAIL_SMTP_USERNAME` — 서비스 전용 Gmail 주소
- `MAIL_SMTP_PASSWORD` — 그 계정의 앱 비밀번호 16자리(공백 없이)
- `MAIL_FROM` — `리트리버 <서비스 Gmail 주소>`
- `PUBLIC_BASE_URL` — 메일 속 링크 주소의 앞부분. 로컬은 `http://127.0.0.1:8000`
- `ALERT_SECRET` — 링크 토큰과 IP 해시에 쓰는 무작위 문자열
- `ALERT_DAILY_SEND_LIMIT` — 선택. 기본 450

앱 비밀번호는 Google 계정에 2단계 인증을 켠 뒤 https://myaccount.google.com/apppasswords
에서 만듭니다. 계정 비밀번호를 바꾸면 앱 비밀번호가 무효가 되어 메일이 멈춥니다.
`ALERT_SECRET`은 다음 명령으로 만듭니다. 값을 바꾸면 이미 보낸 메일의 링크가 모두 무효가 됩니다.

```powershell
uv run python -c "import secrets; print(secrets.token_urlsafe(32))"
```

배포 전에는 DB 없이 메일 한 통만 보내 Gmail 연결과 스팸함 분류를 확인합니다.
실제 메일이 나가므로 받는 주소를 확인한 뒤 실행합니다.

```powershell
uv run python -m retriever_lost_found.alerts.send_test --to 받을주소@example.com
```
````

- [ ] **Step 6: 팀 문서 고치기**

`docs/PRD.md`의 이 줄을

```
- 스케줄: Vercel Cron 2개, 매일 UTC 00:00 = KST 09:00
```

이렇게 바꾸고, 같은 문서 변경 이력 표 끝에 줄을 추가한다.

```
- 스케줄: Vercel Cron 3개 — 수집 2개 매일 UTC 00:00 = KST 09:00, 알림 메일 발송 1개 매일 UTC 02:00 = KST 11:00(③ 스케줄러)
```

```
| 2026-11-06 | 신규 등록 알림(스케줄러) 발송 전용 크론 추가로 부록의 크론을 3개로 갱신 | 팀 결정 |
```

`AGENTS.md` 8장의 엔드포인트 줄을

```
**엔드포인트** — `/` · `/api/geocode` · `/api/institutions`(CDN 5분 캐시) · `/api/found-items`(무캐시) · `/api/places` · `/api/cron/{partner,police}-sync`(Bearer 인증)
```

이렇게 바꾸고, 12장 변경 이력 표 끝에 줄을 추가한다.

```
**엔드포인트** — `/` · `/api/geocode` · `/api/institutions`(CDN 5분 캐시) · `/api/found-items`(무캐시) · `/api/places` · `/api/alerts`·`/api/alerts/{summary,confirm,extend,cancel}`(무캐시, ③ 스케줄러) · `/api/cron/{partner,police}-sync` · `/api/cron/alert-send`(Bearer 인증)
```

```
| 2026-11-06 | 8장 엔드포인트에 알림 예약 API(`/api/alerts*`)와 발송 크론(`/api/cron/alert-send`) 추가 |
```

`docs/team-roles.md` ③ 절의 `**결정 필요** — 아래 항목은 정하기 전에 구현하지 않는다.`부터 `| 발송 상한 | 남용과 비용을 막을 하루 발송 상한 |` 줄까지를 이 내용으로 바꾸고, 5장 변경 이력 표 끝에 줄을 추가한다.

```
**2026-11-06에 정한 것** — 자세한 내용은 `docs/alert-scheduler/2026-11-06-email-scheduler-design.md`.

| 항목 | 결정 |
| --- | --- |
| 발신 수단 | Gmail 앱 비밀번호(SMTP). 서비스 전용 Gmail 계정을 쓰고 설정은 환경 변수(`MAIL_*`)로 받는다. 도메인을 사면 환경 변수만 바꿔 전용 발송 서비스로 옮긴다 |
| 연장 방식 | 메일 속 링크로 한 번에 +7일, 최대 3회(최장 4주). 만료 시각은 등록일 + 7일의 11:00 KST |
| 취소·수신 거부 | 모든 메일에 예약 취소 링크. 알림 메일에 연장 링크, 만료 전날 안내 메일 |
| 개인정보 | 동의 문구에 목적·항목·보유 기간·거부 권리를 적는다(강지현 화면). 예약이 끝나면(만료·취소·미확인 24시간) 이메일과 IP 해시만 지우고 행은 전환율 집계용으로 남긴다 |
| 발송 상한 | 최근 24시간 450통(확인·알림·만료 안내 합계). 같은 이메일 진행 중 예약 3개, 같은 IP 하루 5건 |
```

```
| 2026-11-06 | ③의 "결정 필요" 항목(발신 수단·연장·취소·개인정보·발송 상한)을 결정 사항으로 옮김 |
```

- [ ] **Step 7: 테스트가 통과하는지 확인**

Run: `uv run --with 'pytest>=8,<9' pytest -q`
Expected: 전체 PASS

- [ ] **Step 8: 커밋**

```bash
git add src/retriever_lost_found/alerts/send_test.py tests/alerts/test_alert_send_test.py tests/deployment/test_alert_env.py .env.example .github/workflows/ci.yml README.md docs/PRD.md AGENTS.md docs/team-roles.md
git commit -m "docs: 알림 메일 환경 변수·발송 테스트 명령·크론 3개 반영 — PRD 4.2 P1, AGENTS 6장" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: 유사 판정 연결 — ⑥ 결론 뒤 (차단됨)

> **⑥(김성현, 검색 방식 확정) 결론이 나오기 전에는 시작하지 않는다.** 결론이 나면 바로 착수해 이번 스프린트(~11-09) 안에 알림 메일이 나가게 한다. 검색 화면과 같은 판정 방법을 쓴다(설계 2장 "판정은 같게, 발송 규칙은 따로").

**Files:**
- Create: `src/retriever_lost_found/alerts/judges.py`
- Modify: `src/retriever_lost_found/alerts/matching.py` (`SIMILARITY_JUDGE` 연결)
- Test: `tests/alerts/test_alert_judges.py`

**Interfaces:**
- Consumes: ⑥이 내놓는 판정 — 아래 둘 중 하나
  - (가) Python 함수: 검색어와 물품 하나를 받아 점수를 주는 함수와 통과 기준
  - (나) DB 쪽 판정(임베딩·`pg_trgm` 등): 이때는 `find_new_similar_items`의 속을 DB 함수 호출로 바꾼다
- Produces: `matching.SIMILARITY_JUDGE`가 `None`이 아니게 된다 → 다음 수집부터 매칭이 돈다

- [ ] **Step 1: ⑥ 결론을 확인하고 경우를 고른다**

`docs/team-roles.md` ⑥과 김성현의 PR에서 다음을 확인한다: 판정 함수 이름과 위치, 통과 기준(임계값), 판정이 Python인지 DB인지. 확인한 내용을 이 Task의 Step 2 코드의 `<⑥ 함수>` 자리에 그대로 쓴다. 모르는 것이 있으면 멈추고 김성현에게 묻는다(`AGENTS.md` 6장 — 지어내지 않는다).

- [ ] **Step 2 (경우 가): 판정기 계약 테스트 쓰기**

`tests/alerts/test_alert_judges.py` — 어떤 판정이 오든 지켜야 할 계약이다.

```python
from __future__ import annotations

import unittest

from retriever_lost_found.alerts.judges import search_judge


def candidate(item_id: int, item_name: str) -> dict:
    return {
        "id": item_id, "item_name": item_name, "raw_category_name": "무선이어폰", "color_name": "흰색",
        "description": "", "normalized_search_text": item_name, "registered_on": "2026-11-06",
    }


class SearchJudgeContractTests(unittest.TestCase):
    def test_returns_a_subset_with_scores_between_zero_and_one(self) -> None:
        candidates = [candidate(1, "에어팟 프로"), candidate(2, "검정 장지갑")]

        matched = search_judge("에어팟", candidates)

        self.assertTrue({item["id"] for item, _ in matched} <= {1, 2})
        self.assertIn(1, {item["id"] for item, _ in matched})  # 정확히 같은 이름은 통과
        self.assertNotIn(2, {item["id"] for item, _ in matched})  # 전혀 다른 물품은 제외
        self.assertTrue(all(0.0 <= score <= 1.0 for _, score in matched))

    def test_no_candidates_no_matches(self) -> None:
        self.assertEqual(search_judge("에어팟", []), [])

    def test_same_input_same_result(self) -> None:
        candidates = [candidate(1, "에어팟 프로"), candidate(2, "에어팟 케이스")]

        self.assertEqual(search_judge("에어팟", candidates), search_judge("에어팟", candidates))
```

- [ ] **Step 3 (경우 가): 판정기 쓰기**

`src/retriever_lost_found/alerts/judges.py` — `<⑥ 함수>`와 `<⑥ 기준>`은 Step 1에서 확인한 이름으로 바꾼다.

```python
from __future__ import annotations

from typing import Any

# ⑥(검색 방식 확정) 결론 — 검색 화면과 같은 판정을 쓴다(설계 2장).
from <⑥ 모듈> import <⑥ 함수> as score_item, <⑥ 기준> as threshold_for


def search_judge(query: str, candidates: list[dict[str, Any]]) -> list[tuple[dict[str, Any], float]]:
    cutoff = threshold_for(query)
    scored = [(item, float(score_item(query, item))) for item in candidates]
    return [(item, score) for item, score in scored if score >= cutoff]
```

`src/retriever_lost_found/alerts/matching.py`의 `SIMILARITY_JUDGE: SimilarityJudge | None = None`을 바꾼다.

```python
from .judges import search_judge  # noqa: E402  (⑥ 결론 연결 — Task 11)

SIMILARITY_JUDGE: SimilarityJudge | None = search_judge
```

**경우 (나)라면** Step 2·3 대신: ⑥의 DB 판정 함수를 부르는 `AlertStore` 메서드를 하나 추가하고(`SupabaseAlertStore`와 `FakeAlertStore` 둘 다), `find_new_similar_items`의 속을 그 메서드 호출로 바꾼다. 반환 형식 `MatchResult(items, checked_until)`은 그대로 둔다. `SIMILARITY_JUDGE`에는 그 경로를 쓰는 판정기(예: 후보를 그대로 돌려주는 함수)를 넣어 매칭이 켜지게 한다. 이 경우의 코드는 ⑥의 DB 함수 이름이 정해진 뒤 이 계획에 덧붙인다.

- [ ] **Step 4: 테스트 실행**

Run: `uv run --with 'pytest>=8,<9' pytest -q`
Expected: 전체 PASS. `tests/web/test_cron_endpoint.py`는 수집 결과에 `inserted`가 없어 매칭을 건너뛰므로 그대로 통과한다.

- [ ] **Step 5: 커밋**

```bash
git add src/retriever_lost_found/alerts/judges.py src/retriever_lost_found/alerts/matching.py tests/alerts/test_alert_judges.py
git commit -m "feat: ⑥ 검색 방식을 알림 유사 판정에 연결 — PRD 4.2 P1, Q3" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: 운영 적용 — 사용자 확인이 필요한 단계

> 이 Task의 단계는 모두 운영 DB·실제 메일·배포 설정을 건드린다. **각 단계 전에 사용자에게 무엇을 할지 말하고 허락을 받는다.** 에이전트가 비밀값을 입력하지 않는다.

- [ ] **Step 1: DB 함수 수동 검증** — 로컬 Supabase(`supabase start` → `supabase db reset`)에서 `psql "$LOCAL_DB_URL" -v ON_ERROR_STOP=1 -f tests/database/verify_alert_scheduler.sql`을 실행하고 `알림 스케줄러 DB 검증 통과`가 나오는지 본다. Docker가 없어 로컬 DB를 띄울 수 없으면 멈추고 사용자에게 묻는다(운영 DB에서 돌리지 않는다).
- [ ] **Step 2: 운영 DB에 마이그레이션 적용** — 사용자 허락을 받은 뒤 `supabase db push`. 수집 크론 시간(KST 09:00~09:10)을 피한다. `found_items` 인덱스 생성이 쓰기를 잠깐 막는다.
- [ ] **Step 3: 환경 변수 설정(사용자가 직접)** — 로컬 `sangsang/.env`와 Vercel Production에 `MAIL_*`·`PUBLIC_BASE_URL`·`ALERT_SECRET`. Vercel Preview에는 `MAIL_*`를 넣지 않는다.
- [ ] **Step 4: 발송 테스트(배포 전)** — 사용자가 알려 준 개인 **Gmail 1곳 + 네이버 메일 1곳**으로 `uv run python -m retriever_lost_found.alerts.send_test --to <주소>`를 각각 실행한다. 받은편지함/스팸함(Gmail은 프로모션 탭 포함)을 사용자가 확인한다.
- [ ] **Step 5: PR** — `feat-alert-scheduler` → `main`. 본문에 `test_vercel_config.py` 기대값 변경 이유(11-06 팀 결정), 같은 파일을 고친 다른 담당 작업과의 순서(①과 `web/app.py`), 프런트와 남은 합의(설계 13장)를 적는다.
- [ ] **Step 6: 전체 흐름(배포 후, Task 11과 화면이 붙은 뒤)** — 본인 주소로 실제 예약 1건 → 확인 → 다음 11시대 알림 → 취소. 남는 행 1건은 테스트였다고 `docs/measurements.md`에 적는다.
- [ ] **Step 7: 측정 기록** — 첫 운영 실행 뒤 `alert_runs`에서 예약 1건 매칭 시간, 메일 1통 발송 시간, 실행당 발송 수를 `docs/measurements.md` 형식(0장)대로 남긴다. 450통이 260초 안에 다 나가지 않으면 보조 발송 크론을 제안한다.

---

## 자체 점검 (계획 작성자)

- **설계 대비 빠진 것:** 없음. 설계 4장 → Task 1, 5장 → Task 2·6·7, 6.1 → Task 8, 6.2·6.3 → Task 9, 7장 → Task 4, 8장 → Task 2·3·6·9, 9장 → Task 10, 10장 → 각 Task 테스트·Task 12, 11장 → Task 12 Step 7, 12장 → Task 10 Step 6, 13장 → Task 12 Step 5.
- **자리표시자:** Task 11의 `<⑥ 모듈>`·`<⑥ 함수>`·`<⑥ 기준>`만 남아 있다. ⑥ 결론이 정해야 하는 값이라 의도적으로 비웠고, Step 1에서 채우는 방법을 적었다.
- **이름 일치:** `AlertStore` 메서드 이름·인자는 Task 5 정의를 Task 6(가짜)·8·9가 그대로 쓴다. `run_match_pass(store, judge, *, item_source_code, deadline, clock)`, `run_send_pass(store, sender, settings, *, deadline, clock, now)`, `ALERT_DEADLINE_SECONDS`, `SIMILARITY_JUDGE`, `MailLinks`, `TEST_SETTINGS`, `FakeMonotonic`은 정의한 Task와 쓰는 Task가 같다.
