-- ③ 스케줄러 — 리뷰 반영(2026-11-15). PRD 4.3-E, AGENTS.md 1장·4장.
-- 1) 나중에 기관이 연결된 물품도 알린다. 매칭 커서를 "처음 본 시각(first_seen_at)"이 아니라
--    "검색 투영이 생긴 시각(visible_since)"으로 옮긴다. 수집 때 unmatched였다가 나중에 matched가 되면
--    sync_search_document 트리거가 투영을 새로 넣어 visible_since가 그때 시각이 된다.
--    예약 이후 새 물품만 알린다는 규칙(설계 4.2)은 first_seen_at > 예약 시각으로 따로 지킨다.
-- 2) 실패한 예약(확인 메일 발송 실패·하루 한도)을 지울 때 IP 하루 5건 기록도 함께 지운다.

-- 검색 쪽(김성현) 표에 열 하나를 더한다. 검색 함수는 이 열을 읽지 않고 응답도 바뀌지 않는다.
-- now()는 volatile이 아니어서 기존 행은 다시 쓰지 않고 이 마이그레이션 시각을 값으로 갖는다.
-- 그 결과 진행 중 예약은 다음 매칭에서 예약 이후 물품을 한 번 다시 판정한다. 이미 알린 물품은
-- alert_notified_items로 빠지므로 중복 메일은 없다.
alter table public.found_item_search_documents
    add column visible_since timestamptz not null default now();

create index found_item_search_documents_visible_since_idx
    on public.found_item_search_documents (visible_since);

-- 설계 4.6·16장 — 후보 물품: 장소들 각각의 반경 안 활성 기관(합집합) · 이 출처 · 커서 이후 검색에
-- 보이기 시작함 · 예약 이후 처음 들어옴 · 아직 안 알림. 공간이 1차 필터다(PRD 4.1).
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
        select r.id, r.radius_m, r.created_at, match_cursor.checked_until
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
               item.item_name, item.raw_category_name, document.visible_since
        from reservation
        join public.found_item_search_documents as document
          on document.visible_since > reservation.checked_until
        join scope on scope.id = document.storage_location_id
        join public.found_items as item
          on item.id = document.item_id
         and item.item_source_code = p_item_source_code
         and item.first_seen_at > reservation.created_at
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
            (select max(candidates.visible_since) from candidates),
            (select reservation.checked_until from reservation)
        ),
        'items',
        coalesce(
            (
                select jsonb_agg(to_jsonb(candidates) order by candidates.visible_since, candidates.id)
                from candidates
            ),
            '[]'::jsonb
        )
    )
$$;

-- 설계 5.4 — 확인 메일을 보내지 못한 예약을 지운다. 같은 트랜잭션에서 남긴 IP 기록 한 줄도 지워
-- 실패한 시도가 IP 하루 5건 제한을 깎지 않게 한다. 두 행의 created_at은 같은 now()라 같다.
-- 확인 전(pending) 예약만 지운다 — 확인된 예약은 이 경로로 지울 수 없다.
create or replace function public.discard_alert_reservation(
    p_reservation_id bigint,
    p_ip_hash text
)
returns void
language plpgsql
volatile
security invoker
set search_path = ''
as $$
declare
    v_created_at timestamptz;
begin
    delete from public.alert_reservations
    where id = p_reservation_id
      and status = 'pending'
    returning created_at into v_created_at;

    if found and p_ip_hash is not null then
        delete from public.alert_request_log
        where ctid = (
            select request.ctid
            from public.alert_request_log as request
            where request.ip_hash = p_ip_hash
              and request.created_at = v_created_at
            limit 1
        );
    end if;
end;
$$;

revoke all on function public.alert_candidate_items(bigint, text) from public, anon, authenticated;
grant execute on function public.alert_candidate_items(bigint, text) to service_role;
revoke all on function public.discard_alert_reservation(bigint, text) from public, anon, authenticated;
grant execute on function public.discard_alert_reservation(bigint, text) to service_role;

notify pgrst, 'reload schema';
