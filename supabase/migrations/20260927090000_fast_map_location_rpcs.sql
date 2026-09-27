-- PRD 6 리스크 1행 — found_items가 D1 백필로 약 29만 행이 되자 기존 RPC가
-- 모든 습득물 행을 기관 컬럼(geography 포함)으로 group by하느라 8초 statement_timeout(57014)에
-- 걸려 /api/institutions가 502, 지도의 기관 마커가 전부 사라졌다.
-- 개수는 storage_location_id 부분 인덱스로 먼저 집계하고, 5천여 기관에 붙인다.
-- 반환 형태는 그대로라 배포 순서와 무관하게 적용할 수 있다.
create or replace function public.map_locations_with_item_counts()
returns table (
    id bigint,
    location_source_code text,
    source_key text,
    name text,
    address text,
    phone text,
    display_group text,
    longitude double precision,
    latitude double precision,
    item_count bigint
)
language sql
stable
security invoker
set search_path = ''
as $$
    with item_counts as materialized (
        select item.storage_location_id, count(*)::bigint as item_count
        from public.found_items as item
        where item.storage_location_id is not null
        group by item.storage_location_id
    )
    select
        storage.id,
        storage.location_source_code,
        storage.source_key,
        storage.name,
        storage.address,
        storage.phone,
        source.display_group,
        extensions.st_x(storage.position::extensions.geometry)::double precision as longitude,
        extensions.st_y(storage.position::extensions.geometry)::double precision as latitude,
        coalesce(counts.item_count, 0)::bigint as item_count
    from public.storage_locations as storage
    join public.location_sources as source
      on source.code = storage.location_source_code
    left join item_counts as counts
      on counts.storage_location_id = storage.id
    where storage.is_active
      and source.enabled
$$;

revoke all on function public.map_locations_with_item_counts() from public, anon, authenticated;
grant execute on function public.map_locations_with_item_counts() to service_role;

comment on function public.map_locations_with_item_counts() is
    'Server-only map payload: active locations with coordinates and item counts pre-aggregated per storage_location_id.';

-- PRD 6 — 개수 집계가 실패해도 지도는 보여야 한다. 서버 폴백 전용, found_items를 읽지 않는다.
create or replace function public.map_locations()
returns table (
    id bigint,
    location_source_code text,
    source_key text,
    name text,
    address text,
    phone text,
    display_group text,
    longitude double precision,
    latitude double precision
)
language sql
stable
security invoker
set search_path = ''
as $$
    select
        storage.id,
        storage.location_source_code,
        storage.source_key,
        storage.name,
        storage.address,
        storage.phone,
        source.display_group,
        extensions.st_x(storage.position::extensions.geometry)::double precision as longitude,
        extensions.st_y(storage.position::extensions.geometry)::double precision as latitude
    from public.storage_locations as storage
    join public.location_sources as source
      on source.code = storage.location_source_code
    where storage.is_active
      and source.enabled
$$;

revoke all on function public.map_locations() from public, anon, authenticated;
grant execute on function public.map_locations() to service_role;

comment on function public.map_locations() is
    'Server-only fallback map payload without item counts; used when map_locations_with_item_counts fails.';
