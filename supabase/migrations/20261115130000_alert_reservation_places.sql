-- ③ 스케줄러 — 여러 장소(최대 3곳) 알림. PRD 4.3-E, 설계 16장(2026-11-15 사용자 결정).
-- 예약 하나에 장소 1~3곳을 붙이고 반경은 하나를 함께 쓴다. 장소들 각각의 반경 안 기관을 합쳐 비교한다.
-- 절대 규칙 6 — 새 표도 RLS를 켜고 서버 키(service_role)만 접근한다.

create table public.alert_reservation_places (
    reservation_id bigint not null
        references public.alert_reservations (id) on delete cascade,
    position smallint not null,
    place_name text not null,
    latitude double precision not null,
    longitude double precision not null,
    primary key (reservation_id, position),
    constraint alert_reservation_places_position_range check (position between 1 and 3),
    constraint alert_reservation_places_name_not_blank check (btrim(place_name) <> ''),
    constraint alert_reservation_places_position_valid
        check (latitude between -90 and 90 and longitude between -180 and 180),
    -- 설계 16장 — 좌표가 같은 장소는 하나로 합친다(서버가 합쳐서 보낸다).
    constraint alert_reservation_places_unique_point unique (reservation_id, latitude, longitude)
);

-- 이미 있는 예약(장소 1곳)은 첫 번째 장소로 옮긴다.
insert into public.alert_reservation_places (reservation_id, position, place_name, latitude, longitude)
select id, 1, place_name, latitude, longitude from public.alert_reservations;

-- 장소 칸 3개를 받던 옛 함수를 먼저 지운다. 남겨 두면 지운 칸을 쓰다 실패한다.
drop function public.create_alert_reservation(text, text, text, double precision, double precision, integer, text, text, timestamptz, integer, integer);

alter table public.alert_reservations
    drop constraint alert_reservations_place_not_blank,
    drop constraint alert_reservations_position_valid,
    drop column place_name,
    drop column latitude,
    drop column longitude;

-- 설계 5.4·16장 — 예약 만들기. p_places는 [{place_name, latitude, longitude}] 1~3개(화면 순서).
create or replace function public.create_alert_reservation(
    p_email text,
    p_query text,
    p_places jsonb,
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
    -- 서버(alerts/reservations.py)가 먼저 검사한다. 여기서는 잘못된 호출을 막기만 한다.
    if jsonb_typeof(p_places) is distinct from 'array' then
        raise exception 'alert places must be an array';
    end if;
    if jsonb_array_length(p_places) not between 1 and 3 then
        raise exception 'alert places must be 1 to 3';
    end if;

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
        email, query, radius_m, status, link_nonce, consent_at, expires_at
    )
    values (
        p_email, btrim(p_query), p_radius_m, 'pending', p_link_nonce, now(), p_expires_at
    )
    returning id into v_reservation_id;

    insert into public.alert_reservation_places (reservation_id, position, place_name, latitude, longitude)
    select v_reservation_id,
           place.position::smallint,
           btrim(place.value ->> 'place_name'),
           (place.value ->> 'latitude')::double precision,
           (place.value ->> 'longitude')::double precision
    from jsonb_array_elements(p_places) with ordinality as place(value, position);

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

-- 설계 4.6·16장 — 후보 물품: 장소들 각각의 반경 안 활성 기관(합집합) · 이 출처 · 커서 이후 처음 들어옴 ·
-- 아직 안 알림. 공간이 1차 필터다(PRD 4.1). 기관 목록은 저장하지 않고 매번 다시 계산한다.
-- 판정(alerts/judge.py)이 운영 검색과 같은 글을 보도록 검색 투영(found_item_search_documents)의
-- search_text를 keyword_found_item_candidates와 같은 필드와 함께 돌려준다. 투영은 matched 물품에만 있다.
create or replace function public.alert_candidate_items(
    p_reservation_id bigint,
    p_item_source_code text
)
returns jsonb
language sql
stable
set search_path = ''
security invoker
as $$
    with reservation as (
        select r.id, r.radius_m, match_cursor.checked_until
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
        -- 두 장소의 반경에 함께 들어간 기관도 한 번만 센다.
        select distinct location.id
        from reservation
        join public.alert_reservation_places as place on place.reservation_id = reservation.id
        join public.storage_locations as location
          on location.is_active
         and extensions.st_dwithin(
             location.position,
             extensions.st_setsrid(
                 extensions.st_makepoint(place.longitude, place.latitude), 4326
             )::extensions.geography,
             reservation.radius_m, false
         )
    ),
    candidates as (
        select item.id, item.storage_location_id, document.search_text,
               item.item_name, item.raw_category_name, item.first_seen_at
        from reservation
        join public.found_items as item
          on item.item_source_code = p_item_source_code
         and item.first_seen_at > reservation.checked_until
        join scope on scope.id = item.storage_location_id
        join public.found_item_search_documents as document on document.item_id = item.id
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

-- 설계 6.2·16장 — 메일 한 통을 만들 내용. 물품은 점수 높은 순, 같으면 최근 등록 순.
-- 장소는 화면 순서대로, 물품마다 보관기관에서 가장 가까운 장소 이름(near_place_name)을 붙인다.
create or replace function public.alert_message_payload(p_message_id bigint)
returns jsonb
language sql
stable
set search_path = ''
security invoker
as $$
    select jsonb_build_object(
        'message', to_jsonb(message),
        'reservation', jsonb_build_object(
            'id', reservation.id,
            'email', reservation.email,
            'query', reservation.query,
            'places', coalesce(
                (
                    select jsonb_agg(jsonb_build_object('name', place.place_name) order by place.position)
                    from public.alert_reservation_places as place
                    where place.reservation_id = reservation.id
                ),
                '[]'::jsonb
            ),
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
                        'near_place_name', (
                            select place.place_name
                            from public.alert_reservation_places as place
                            where place.reservation_id = reservation.id
                              and location.position is not null
                            order by extensions.st_distance(
                                location.position,
                                extensions.st_setsrid(
                                    extensions.st_makepoint(place.longitude, place.latitude), 4326
                                )::extensions.geography,
                                false
                            ), place.position
                            limit 1
                        ),
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

alter table public.alert_reservation_places enable row level security;

revoke all on table public.alert_reservation_places from public, anon, authenticated;
grant select, insert, update, delete on table public.alert_reservation_places to service_role;

revoke all on function public.create_alert_reservation(text, text, jsonb, integer, text, text, timestamptz, integer, integer) from public, anon, authenticated;
grant execute on function public.create_alert_reservation(text, text, jsonb, integer, text, text, timestamptz, integer, integer) to service_role;
revoke all on function public.alert_candidate_items(bigint, text) from public, anon, authenticated;
grant execute on function public.alert_candidate_items(bigint, text) to service_role;
revoke all on function public.alert_message_payload(bigint) from public, anon, authenticated;
grant execute on function public.alert_message_payload(bigint) to service_role;

-- PostgREST가 예약 → 장소 관계(get_reservation의 embed)를 바로 알게 한다.
notify pgrst, 'reload schema';
