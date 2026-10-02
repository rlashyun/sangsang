-- PRD 4.5: detailed Lost112 evidence disambiguates repeated institution names.
alter table public.found_items
    add column detail_org_id text,
    add column detail_org_name text,
    add column detail_department text,
    add column detail_phone text,
    add column detail_checked_at timestamptz;

create table public.storage_location_detail_mappings (
    item_source_code text not null references public.item_sources(code),
    normalized_raw_storage_name text not null,
    detail_org_id text not null,
    storage_location_id bigint not null references public.storage_locations(id),
    evidence_note text not null,
    verified_at timestamptz not null default now(),
    primary key (item_source_code, normalized_raw_storage_name, detail_org_id),
    check (btrim(normalized_raw_storage_name) <> ''),
    check (btrim(detail_org_id) <> ''),
    check (btrim(evidence_note) <> '')
);

create index storage_location_detail_mappings_location_idx
    on public.storage_location_detail_mappings (storage_location_id);

alter table public.storage_location_detail_mappings enable row level security;
revoke all on table public.storage_location_detail_mappings from public, anon, authenticated;
grant select, insert, update, delete on table public.storage_location_detail_mappings to service_role;

-- PRD 4.5: keep verified detail matches during re-ingestion, including older deployments.
create function public.resolve_verified_detail_location()
returns trigger language plpgsql security invoker set search_path = '' as $$
declare
    target_id bigint;
begin
    if tg_op = 'UPDATE' then
        if new.raw_storage_name is distinct from old.raw_storage_name
           and new.detail_checked_at is not distinct from old.detail_checked_at then
            new.detail_org_id := null;
            new.detail_org_name := null;
            new.detail_department := null;
            new.detail_phone := null;
            new.detail_checked_at := null;
        elsif new.raw_storage_name = old.raw_storage_name
              and new.detail_checked_at is null then
            new.detail_org_id := old.detail_org_id;
            new.detail_org_name := old.detail_org_name;
            new.detail_department := old.detail_department;
            new.detail_phone := old.detail_phone;
            new.detail_checked_at := old.detail_checked_at;
        end if;
    end if;
    if new.location_match_status = 'ambiguous' and new.detail_org_id is not null then
        select m.storage_location_id into target_id
        from public.storage_location_detail_mappings m
        join public.storage_locations l on l.id = m.storage_location_id and l.is_active
        where m.item_source_code = new.item_source_code
          and m.normalized_raw_storage_name = pg_catalog.regexp_replace(
              pg_catalog.lower(pg_catalog.normalize(new.raw_storage_name, 'NFKC')),
              '[^[:alnum:]]', '', 'g')
          and m.detail_org_id = new.detail_org_id
          and l.location_source_code = case new.item_source_code
              when 'police_api' then 'esri_police' when 'partner_api' then 'partner_csv' end;
        if target_id is not null then
            new.storage_location_id := target_id;
            new.location_match_status := 'matched';
        end if;
    end if;
    return new;
end;
$$;
revoke all on function public.resolve_verified_detail_location() from public, anon, authenticated;
grant execute on function public.resolve_verified_detail_location() to service_role;
create trigger found_items_resolve_verified_detail
    before insert or update on public.found_items
    for each row execute function public.resolve_verified_detail_location();

-- PRD 6: both Goseong station coordinates in the source catalogue were wrong.
-- Official addresses: gwpolice.go.kr/gs/sub05/sub05_02.jsp and gnpolice.go.kr/gs/.
update public.storage_locations
set address = '강원특별자치도 고성군 간성읍 탑동길 12',
    position = extensions.st_geogfromtext('SRID=4326;POINT(128.476884366701 38.3765562448967)')
where location_source_code = 'esri_police' and source_key = 'station:141';

update public.storage_locations
set address = '경상남도 고성군 고성읍 중앙로 113',
    position = extensions.st_geogfromtext('SRID=4326;POINT(128.333663847157 34.9759201780233)')
where location_source_code = 'esri_police' and source_key = 'station:250';

-- PRD 4.5: Lost112 phone prefixes and official station addresses distinguish Goseong.
insert into public.storage_location_detail_mappings
    (item_source_code, normalized_raw_storage_name, detail_org_id, storage_location_id, evidence_note)
select 'police_api', '고성경찰서', 'O0001173', id,
       'Lost112 033-680-6347; official https://www.gwpolice.go.kr/gs/sub05/sub05_02.jsp'
from public.storage_locations where location_source_code = 'esri_police' and source_key = 'station:141';

insert into public.storage_location_detail_mappings
    (item_source_code, normalized_raw_storage_name, detail_org_id, storage_location_id, evidence_note)
select 'police_api', '고성경찰서', 'O0002322', id,
       'Lost112 055-647-3247; official https://gnpolice.go.kr/gnpsub/page.do?MENU_ID=it03&go=gs'
from public.storage_locations where location_source_code = 'esri_police' and source_key = 'station:250';

-- Suyeong Police Station took over Gwangnam from Busan Nambu in 2025.
insert into public.storage_location_detail_mappings
    (item_source_code, normalized_raw_storage_name, detail_org_id, storage_location_id, evidence_note)
select 'police_api', '광남지구대', 'O0003307', id,
       'Lost112 부산수영경찰서; official https://www.bspolice.go.kr/suyeong/view.do?no=1785'
from public.storage_locations where location_source_code = 'esri_police' and source_key = 'substation:319';
