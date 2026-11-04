-- PRD 3.3 · 5 — 검색어 없는 습득물 목록을 한 페이지씩 돌려준다.
-- 기존에는 반경 안 습득물 전체(서울역 3km 37,231건, 응답 26MB)를 1,000건씩 offset으로
-- 38번 받아 왔고, 페이지마다 DB가 전체 후보를 다시 정렬해 16~26초가 걸렸다(2026-10-07 실측).
--
-- 스코프는 그대로 브라우저가 고른 기관 ID 묶음이다(PRD 4.5 반경 필터).
-- 기관마다 최신순 인덱스에서 앞쪽 limit건만 읽고, 그 합집합에서 다시 limit건을 고른 뒤
-- 그 행들만 본문 컬럼을 읽는다. 후보 전체를 정렬하거나 raw_payload가 든 행 전체를 읽지 않는다.
-- 다음 페이지는 offset이 아니라 마지막 행의 (registered_on, id) 커서로 넘긴다.

-- 기존 found_items_location_category_date_idx는 category_id가 가운데 있어
-- 기관 안에서 registered_on desc, id desc 순서로 바로 읽을 수 없다.
create index if not exists found_items_location_registered_id_idx
    on public.found_items (storage_location_id, registered_on desc, id desc)
    where storage_location_id is not null;

create or replace function public.found_items_page(
    p_location_ids bigint[],
    p_limit integer,
    p_before_registered_on date default null,
    p_before_id bigint default null,
    p_include_total boolean default false
)
returns jsonb
language sql
stable
security invoker
set search_path = ''
as $$
    with scope as (
        select distinct location_id as storage_location_id
        from unnest(p_location_ids) as location_id
        where location_id is not null
    ),
    page_keys as (
        select candidate.id, candidate.registered_on
        from scope
        cross join lateral (
            select item.id, item.registered_on
            from public.found_items as item
            where item.storage_location_id = scope.storage_location_id
              and (
                  p_before_registered_on is null
                  or (item.registered_on, item.id) < (p_before_registered_on, p_before_id)
              )
            order by item.registered_on desc, item.id desc
            limit greatest(1, least(p_limit, 201))
        ) as candidate
        order by candidate.registered_on desc, candidate.id desc
        limit greatest(1, least(p_limit, 201))
    )
    select jsonb_build_object(
        'items',
        coalesce(
            (
                select jsonb_agg(
                    jsonb_build_object(
                        'id', item.id,
                        'storage_location_id', item.storage_location_id,
                        'item_source_code', item.item_source_code,
                        'atc_id', item.atc_id,
                        'found_sequence', item.found_sequence,
                        'raw_category_name', item.raw_category_name,
                        'color_name', item.color_name,
                        'item_name', item.item_name,
                        'description', item.description,
                        'image_url', item.image_url,
                        'raw_storage_name', item.raw_storage_name,
                        'registered_on', item.registered_on,
                        'normalized_search_text', item.normalized_search_text
                    )
                    order by item.registered_on desc, item.id desc
                )
                from page_keys
                join public.found_items as item
                  on item.id = page_keys.id
            ),
            '[]'::jsonb
        ),
        -- 첫 페이지에서만 센다. 화면의 "현재 보관 중인 물품 N건" 문구에 쓴다.
        'total_count',
        case
            when p_include_total then (
                select count(*)
                from public.found_items as item
                where item.storage_location_id = any(p_location_ids)
            )
        end
    )
$$;

revoke all on function public.found_items_page(bigint[], integer, date, bigint, boolean)
    from public, anon, authenticated;
grant execute on function public.found_items_page(bigint[], integer, date, bigint, boolean)
    to service_role;

comment on function public.found_items_page(bigint[], integer, date, bigint, boolean) is
    'Server-only paged found-item list for selected storage locations, newest first, keyset cursor on (registered_on, id).';
