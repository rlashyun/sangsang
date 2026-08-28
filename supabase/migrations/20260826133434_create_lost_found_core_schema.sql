create extension if not exists postgis with schema extensions;
create extension if not exists vector with schema extensions;

create table public.item_sources (
    code text primary key,
    name text not null,
    retention_days smallint not null check (retention_days > 0),
    enabled boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint item_sources_code_not_blank check (btrim(code) <> ''),
    constraint item_sources_name_not_blank check (btrim(name) <> '')
);

create table public.location_sources (
    code text primary key,
    name text not null,
    display_group text not null check (display_group in ('partner', 'police')),
    enabled boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint location_sources_code_not_blank check (btrim(code) <> ''),
    constraint location_sources_name_not_blank check (btrim(name) <> '')
);

create table public.storage_locations (
    id bigint generated always as identity primary key,
    location_source_code text not null
        references public.location_sources(code) on update cascade on delete restrict,
    source_key text not null,
    name text not null,
    normalized_name text not null,
    location_kind text,
    address text,
    phone text,
    region_code text not null default '',
    position extensions.geography(point, 4326) not null,
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint storage_locations_source_key_not_blank check (btrim(source_key) <> ''),
    constraint storage_locations_name_not_blank check (btrim(name) <> ''),
    constraint storage_locations_normalized_name_not_blank check (btrim(normalized_name) <> ''),
    constraint storage_locations_source_key_unique unique (location_source_code, source_key)
);

create table public.storage_location_aliases (
    id bigint generated always as identity primary key,
    storage_location_id bigint not null
        references public.storage_locations(id) on delete cascade,
    item_source_code text not null
        references public.item_sources(code) on update cascade on delete restrict,
    alias_name text not null,
    normalized_alias text not null,
    region_code text not null default '',
    match_method text not null default 'manual'
        check (match_method in ('exact', 'normalized', 'manual', 'source_code')),
    verified boolean not null default false,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint storage_location_aliases_alias_not_blank check (btrim(alias_name) <> ''),
    constraint storage_location_aliases_normalized_not_blank check (btrim(normalized_alias) <> ''),
    constraint storage_location_aliases_scope_unique
        unique (item_source_code, normalized_alias, region_code)
);

create table public.item_categories (
    id smallint generated always as identity primary key,
    code text not null unique,
    name text not null unique,
    sort_order smallint not null,
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint item_categories_code_not_blank check (btrim(code) <> ''),
    constraint item_categories_name_not_blank check (btrim(name) <> ''),
    constraint item_categories_sort_order_nonnegative check (sort_order >= 0)
);

create table public.category_mappings (
    item_source_code text not null
        references public.item_sources(code) on update cascade on delete cascade,
    raw_category_name text not null,
    normalized_raw_category text not null,
    category_id smallint not null
        references public.item_categories(id) on delete restrict,
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (item_source_code, raw_category_name),
    constraint category_mappings_raw_not_blank check (btrim(raw_category_name) <> ''),
    constraint category_mappings_normalized_not_blank check (btrim(normalized_raw_category) <> ''),
    constraint category_mappings_normalized_unique
        unique (item_source_code, normalized_raw_category)
);

create table public.found_items (
    id bigint generated always as identity primary key,
    item_source_code text not null
        references public.item_sources(code) on update cascade on delete restrict,
    atc_id text not null,
    found_sequence text not null,
    storage_location_id bigint
        references public.storage_locations(id) on delete restrict,
    location_match_status text not null default 'unmatched'
        check (location_match_status in ('matched', 'unmatched', 'ambiguous')),
    category_id smallint not null
        references public.item_categories(id) on delete restrict,
    raw_category_name text not null,
    color_name text,
    item_name text not null default '',
    description text not null default '',
    image_url text,
    raw_storage_name text not null default '',
    registered_on date not null,
    normalized_search_text text not null default '',
    raw_payload jsonb not null default '{}'::jsonb,
    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint found_items_source_identity_unique
        unique (item_source_code, atc_id, found_sequence),
    constraint found_items_atc_id_not_blank check (btrim(atc_id) <> ''),
    constraint found_items_found_sequence_not_blank check (btrim(found_sequence) <> ''),
    constraint found_items_raw_category_not_blank check (btrim(raw_category_name) <> ''),
    constraint found_items_location_match_consistent check (
        (location_match_status = 'matched' and storage_location_id is not null)
        or
        (location_match_status in ('unmatched', 'ambiguous') and storage_location_id is null)
    ),
    constraint found_items_seen_order_valid check (last_seen_at >= first_seen_at)
);

create table public.ingestion_runs (
    id bigint generated always as identity primary key,
    item_source_code text not null
        references public.item_sources(code) on update cascade on delete restrict,
    started_at timestamptz not null default now(),
    finished_at timestamptz,
    status text not null default 'running'
        check (status in ('running', 'succeeded', 'partial', 'failed')),
    fetched_count integer not null default 0 check (fetched_count >= 0),
    selected_count integer not null default 0 check (selected_count >= 0),
    inserted_count integer not null default 0 check (inserted_count >= 0),
    updated_count integer not null default 0 check (updated_count >= 0),
    deleted_count integer not null default 0 check (deleted_count >= 0),
    unmatched_count integer not null default 0 check (unmatched_count >= 0),
    invalid_count integer not null default 0 check (invalid_count >= 0),
    error_message text,
    metadata jsonb not null default '{}'::jsonb,
    constraint ingestion_runs_finished_state_valid check (
        (status = 'running' and finished_at is null)
        or
        (status <> 'running' and finished_at is not null)
    )
);

create index storage_locations_location_source_idx
    on public.storage_locations (location_source_code);
create index storage_locations_region_active_idx
    on public.storage_locations (region_code, is_active);
create index storage_locations_position_active_gist
    on public.storage_locations using gist (position)
    where is_active;
create index storage_location_aliases_location_idx
    on public.storage_location_aliases (storage_location_id);
create index storage_location_aliases_source_normalized_idx
    on public.storage_location_aliases (item_source_code, normalized_alias);
create index category_mappings_category_idx
    on public.category_mappings (category_id);
create index found_items_location_category_date_idx
    on public.found_items (storage_location_id, category_id, registered_on desc)
    where storage_location_id is not null;
create index found_items_source_registered_idx
    on public.found_items (item_source_code, registered_on);
create index found_items_category_registered_idx
    on public.found_items (category_id, registered_on desc);
create index found_items_location_match_status_idx
    on public.found_items (location_match_status)
    where location_match_status <> 'matched';
create index ingestion_runs_source_started_idx
    on public.ingestion_runs (item_source_code, started_at desc);
create unique index ingestion_runs_one_running_per_source
    on public.ingestion_runs (item_source_code)
    where status = 'running';

create or replace function public.set_row_updated_at()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

create trigger item_sources_set_updated_at
before update on public.item_sources
for each row execute function public.set_row_updated_at();
create trigger location_sources_set_updated_at
before update on public.location_sources
for each row execute function public.set_row_updated_at();
create trigger storage_locations_set_updated_at
before update on public.storage_locations
for each row execute function public.set_row_updated_at();
create trigger storage_location_aliases_set_updated_at
before update on public.storage_location_aliases
for each row execute function public.set_row_updated_at();
create trigger item_categories_set_updated_at
before update on public.item_categories
for each row execute function public.set_row_updated_at();
create trigger category_mappings_set_updated_at
before update on public.category_mappings
for each row execute function public.set_row_updated_at();
create trigger found_items_set_updated_at
before update on public.found_items
for each row execute function public.set_row_updated_at();

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

create trigger found_items_set_seen_at
before insert or update on public.found_items
for each row execute function public.set_found_item_seen_at();

create or replace function public.delete_expired_found_items(p_source_code text default null)
returns table (source_code text, deleted_count bigint)
language sql
security invoker
set search_path = ''
as $$
    with target_sources as materialized (
        select s.code, s.retention_days
        from public.item_sources as s
        where s.enabled
          and (p_source_code is null or s.code = p_source_code)
    ),
    deleted as (
        delete from public.found_items as fi
        using target_sources as ts
        where fi.item_source_code = ts.code
          and fi.registered_on < current_date - (ts.retention_days::integer - 1)
        returning fi.item_source_code
    )
    select ts.code, count(d.item_source_code)::bigint
    from target_sources as ts
    left join deleted as d on d.item_source_code = ts.code
    group by ts.code
    order by ts.code;
$$;

comment on table public.item_sources is
    'External found-item APIs. retention_days controls registration-date retention.';
comment on table public.location_sources is
    'Static location datasets, separated from found-item API sources.';
comment on table public.storage_locations is
    'Partner institutions and police locations stored as PostGIS points.';
comment on table public.storage_location_aliases is
    'Maps API depPlace variants to canonical storage locations.';
comment on table public.item_categories is
    'Canonical service categories used by ingestion and search.';
comment on table public.category_mappings is
    'Exact prdtClNm-to-category mappings used by ingestion code.';
comment on table public.found_items is
    'Only validated and selected found items are stored here.';
comment on table public.ingestion_runs is
    'Audit log for scheduled API ingestion and retention cleanup.';
comment on function public.delete_expired_found_items(text) is
    'Deletes found items outside each enabled source retention window. Call only after a successful source sync.';

alter table public.item_sources enable row level security;
alter table public.location_sources enable row level security;
alter table public.storage_locations enable row level security;
alter table public.storage_location_aliases enable row level security;
alter table public.item_categories enable row level security;
alter table public.category_mappings enable row level security;
alter table public.found_items enable row level security;
alter table public.ingestion_runs enable row level security;

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

revoke all on function public.set_row_updated_at() from public, anon, authenticated;
grant execute on function public.set_row_updated_at() to service_role;
revoke all on function public.set_found_item_seen_at() from public, anon, authenticated;
grant execute on function public.set_found_item_seen_at() to service_role;
revoke all on function public.delete_expired_found_items(text) from public, anon, authenticated;
grant execute on function public.delete_expired_found_items(text) to service_role;

insert into public.item_sources (code, name, retention_days, enabled) values
    ('partner_api', '유실물 연계기관 습득물 API', 10, true),
    ('police_api', '경찰관서 습득물 API', 20, true);

insert into public.location_sources (code, name, display_group, enabled) values
    ('partner_csv', '유실물 연계기관 좌표 CSV', 'partner', true),
    ('esri_police', '전국 경찰관서 Esri 좌표', 'police', true);

insert into public.item_categories (id, code, name, sort_order, is_active)
overriding system value
values
    (1, 'bag', '가방', 10, true),
    (2, 'clothing', '의류', 20, true),
    (3, 'electronics', '전자기기', 30, true),
    (4, 'wallet', '지갑', 40, true),
    (5, 'mobile_phone', '휴대폰', 50, true),
    (6, 'card', '카드', 60, true);

select setval(
    pg_get_serial_sequence('public.item_categories', 'id'),
    (select max(id) from public.item_categories),
    true
);

insert into public.category_mappings
    (item_source_code, raw_category_name, normalized_raw_category, category_id, is_active)
values
    ('partner_api', '가방 > 기타가방', '가방기타가방', 1, true),
    ('partner_api', '가방 > 남성용가방', '가방남성용가방', 1, true),
    ('partner_api', '가방 > 여성용가방', '가방여성용가방', 1, true),
    ('partner_api', '의류 > 모자', '의류모자', 2, true),
    ('partner_api', '전자기기 > 무선이어폰', '전자기기무선이어폰', 3, true),
    ('partner_api', '지갑 > 기타 지갑', '지갑기타지갑', 4, true),
    ('partner_api', '지갑 > 남성용 지갑', '지갑남성용지갑', 4, true),
    ('partner_api', '지갑 > 여성용 지갑', '지갑여성용지갑', 4, true),
    ('partner_api', '카드 > 교통카드', '카드교통카드', 6, true),
    ('partner_api', '카드 > 기타카드', '카드기타카드', 6, true),
    ('partner_api', '카드 > 신용(체크)카드', '카드신용체크카드', 6, true),
    ('partner_api', '카드 > 일반카드', '카드일반카드', 6, true),
    ('partner_api', '휴대폰 > LG휴대폰', '휴대폰lg휴대폰', 5, true),
    ('partner_api', '휴대폰 > 기타통신기기', '휴대폰기타통신기기', 5, true),
    ('partner_api', '휴대폰 > 기타휴대폰', '휴대폰기타휴대폰', 5, true),
    ('partner_api', '휴대폰 > 삼성휴대폰', '휴대폰삼성휴대폰', 5, true),
    ('partner_api', '휴대폰 > 아이폰', '휴대폰아이폰', 5, true),
    ('police_api', '가방 > 기타가방', '가방기타가방', 1, true),
    ('police_api', '가방 > 남성용가방', '가방남성용가방', 1, true),
    ('police_api', '가방 > 여성용가방', '가방여성용가방', 1, true),
    ('police_api', '의류 > 모자', '의류모자', 2, true),
    ('police_api', '전자기기 > 무선이어폰', '전자기기무선이어폰', 3, true),
    ('police_api', '지갑 > 기타 지갑', '지갑기타지갑', 4, true),
    ('police_api', '지갑 > 남성용 지갑', '지갑남성용지갑', 4, true),
    ('police_api', '지갑 > 여성용 지갑', '지갑여성용지갑', 4, true),
    ('police_api', '카드 > 교통카드', '카드교통카드', 6, true),
    ('police_api', '카드 > 기타카드', '카드기타카드', 6, true),
    ('police_api', '카드 > 신용(체크)카드', '카드신용체크카드', 6, true),
    ('police_api', '카드 > 일반카드', '카드일반카드', 6, true),
    ('police_api', '휴대폰 > LG휴대폰', '휴대폰lg휴대폰', 5, true),
    ('police_api', '휴대폰 > 기타통신기기', '휴대폰기타통신기기', 5, true),
    ('police_api', '휴대폰 > 기타휴대폰', '휴대폰기타휴대폰', 5, true),
    ('police_api', '휴대폰 > 삼성휴대폰', '휴대폰삼성휴대폰', 5, true),
    ('police_api', '휴대폰 > 아이폰', '휴대폰아이폰', 5, true);
