-- PRD 3.1-4 · 5 — 기존 기관 집계 결과를 한 번의 RPC 응답으로 전달한다.
create function public.map_locations_with_item_counts_json()
returns json
language sql
stable
security invoker
set search_path = ''
as $$
    select coalesce(
        json_agg(location order by location.location_source_code, location.source_key),
        '[]'::json
    )
    from public.map_locations_with_item_counts() as location
$$;

revoke all on function public.map_locations_with_item_counts_json()
    from public, anon, authenticated;
grant execute on function public.map_locations_with_item_counts_json()
    to service_role;
