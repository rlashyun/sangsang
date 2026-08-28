create unique index if not exists ingestion_runs_one_running_per_source
    on public.ingestion_runs (item_source_code)
    where status = 'running';

comment on table public.item_categories is
    'Canonical service categories used by ingestion and search.';
comment on table public.category_mappings is
    'Exact prdtClNm-to-category mappings used by ingestion code.';

alter default privileges for role postgres in schema public
    revoke all on tables from public, anon, authenticated;
alter default privileges for role postgres in schema public
    revoke all on sequences from public, anon, authenticated;
alter default privileges for role postgres in schema public
    revoke execute on functions from public, anon, authenticated;

revoke all on table
    public.item_sources,
    public.location_sources,
    public.storage_locations,
    public.storage_location_aliases,
    public.item_categories,
    public.category_mappings,
    public.found_items,
    public.ingestion_runs
from public, anon, authenticated;

grant select, insert, update, delete on table
    public.item_sources,
    public.location_sources,
    public.storage_locations,
    public.storage_location_aliases,
    public.item_categories,
    public.category_mappings,
    public.found_items,
    public.ingestion_runs
to service_role;

revoke all on sequence
    public.storage_locations_id_seq,
    public.storage_location_aliases_id_seq,
    public.item_categories_id_seq,
    public.found_items_id_seq,
    public.ingestion_runs_id_seq
from public, anon, authenticated;

grant usage, select on sequence
    public.storage_locations_id_seq,
    public.storage_location_aliases_id_seq,
    public.item_categories_id_seq,
    public.found_items_id_seq,
    public.ingestion_runs_id_seq
to service_role;

create or replace function public.set_found_item_seen_at()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
    if tg_op = 'INSERT' then
        new.first_seen_at = now();
    else
        new.first_seen_at = old.first_seen_at;
    end if;
    new.last_seen_at = now();
    return new;
end;
$$;

drop trigger if exists found_items_set_seen_at on public.found_items;
create trigger found_items_set_seen_at
before insert or update on public.found_items
for each row execute function public.set_found_item_seen_at();

revoke all on function public.set_found_item_seen_at() from public, anon, authenticated;
grant execute on function public.set_found_item_seen_at() to service_role;

drop function if exists public.map_location_item_counts();
drop function if exists public.map_locations_with_item_counts();

create function public.map_locations_with_item_counts()
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
        count(item.id)::bigint as item_count
    from public.storage_locations as storage
    join public.location_sources as source
      on source.code = storage.location_source_code
    left join public.found_items as item
      on item.storage_location_id = storage.id
    where storage.is_active
      and source.enabled
    group by
        storage.id,
        storage.location_source_code,
        storage.source_key,
        storage.name,
        storage.address,
        storage.phone,
        storage.position,
        source.display_group
$$;

revoke all on function public.map_locations_with_item_counts() from public, anon, authenticated;
grant execute on function public.map_locations_with_item_counts() to service_role;

comment on function public.map_locations_with_item_counts() is
    'Server-only map payload containing active locations, coordinates, source display group, and selected-category item counts.';
