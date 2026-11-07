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
    -- 설계 8장 — 끝난 예약에는 이메일이 남을 수 없다. IP 해시는 예약 행에 두지 않는다(alert_request_log).
    constraint alert_reservations_personal_data_lifecycle check (
        (status in ('pending', 'active') and email is not null)
        or (status in ('expired', 'cancelled', 'unconfirmed_expired') and email is null)
    )
);

create index alert_reservations_status_expires_idx
    on public.alert_reservations (status, expires_at);
create index alert_reservations_open_email_idx
    on public.alert_reservations (email)
    where status in ('pending', 'active');

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

-- 설계 14장 — IP당 24시간 5건 제한용 기록. 예약 행과 따로 두어 취소해도 제한이 풀리지 않게 하고,
-- IP 해시는 24시간이 지나면 지운다(예약 생성과 발송 크론 정리 때).
create table public.alert_request_log (
    ip_hash text not null,
    created_at timestamptz not null default now()
);

create index alert_request_log_ip_created_idx
    on public.alert_request_log (ip_hash, created_at);
create index alert_request_log_created_idx
    on public.alert_request_log (created_at);

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

    -- 24시간이 지난 IP 해시는 남기지 않는다.
    delete from public.alert_request_log where created_at <= now() - interval '24 hours';

    -- 취소·만료와 상관없이 이 IP가 24시간 안에 만든 예약 수를 센다(취소로 제한을 풀 수 없다).
    if p_ip_hash is not null and (
        select count(*)
        from public.alert_request_log as request
        where request.ip_hash = p_ip_hash
    ) >= p_ip_daily_limit then
        return jsonb_build_object('status', 'ip_limit');
    end if;

    insert into public.alert_reservations (
        email, query, place_name, latitude, longitude, radius_m,
        status, link_nonce, consent_at, expires_at
    )
    values (
        p_email, btrim(p_query), btrim(p_place_name), p_latitude, p_longitude, p_radius_m,
        'pending', p_link_nonce, now(), p_expires_at
    )
    returning id into v_reservation_id;

    if p_ip_hash is not null then
        insert into public.alert_request_log (ip_hash) values (p_ip_hash);
    end if;

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
    set status = 'cancelled', email = null, ended_at = now()
    where id = p_reservation_id;
    delete from public.alert_messages
    where reservation_id = p_reservation_id
      and status = 'pending';
    return 'cancelled';
end;
$$;

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

-- 설계 6.2 ③ — 만료된 예약과 확인 없이 24시간 지난 예약의 이메일을 지운다. 행은 전환율 집계용으로 남긴다.
-- 24시간이 지난 IP 해시 기록도 지운다.
create or replace function public.close_ended_alert_reservations()
returns jsonb
language sql
volatile
security invoker
set search_path = ''
as $$
    with expired as (
        update public.alert_reservations
        set status = 'expired', email = null, ended_at = now()
        where status = 'active'
          and expires_at <= now()
        returning id
    ),
    unconfirmed as (
        update public.alert_reservations
        set status = 'unconfirmed_expired', email = null, ended_at = now()
        where status = 'pending'
          and created_at <= now() - interval '24 hours'
        returning id
    ),
    dropped as (
        delete from public.alert_messages
        where status = 'pending'
          and reservation_id in (select id from expired union all select id from unconfirmed)
        returning id
    ),
    purged as (
        delete from public.alert_request_log
        where created_at <= now() - interval '24 hours'
        returning 1
    )
    select jsonb_build_object(
        'expired', (select count(*) from expired),
        'unconfirmed_closed', (select count(*) from unconfirmed),
        'messages_dropped', (select count(*) from dropped),
        'request_log_purged', (select count(*) from purged)
    )
$$;

alter table public.alert_reservations enable row level security;
alter table public.alert_match_cursors enable row level security;
alter table public.alert_messages enable row level security;
alter table public.alert_notified_items enable row level security;
alter table public.alert_runs enable row level security;
alter table public.alert_request_log enable row level security;

revoke all on table
    public.alert_reservations,
    public.alert_match_cursors,
    public.alert_messages,
    public.alert_notified_items,
    public.alert_runs,
    public.alert_request_log
from public, anon, authenticated;

grant select, insert, update, delete on table
    public.alert_reservations,
    public.alert_match_cursors,
    public.alert_messages,
    public.alert_notified_items,
    public.alert_runs,
    public.alert_request_log
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
