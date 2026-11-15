-- PRD 4.5, 6 — 2026-11-06: verified institution codes for the reviewed ambiguous backlog.
-- Codes are populated separately from current detail evidence; never copy legacy mappings blindly.
alter table public.storage_locations
    add column detail_code text check (detail_code is null or btrim(detail_code) <> '');
create unique index storage_locations_detail_code_unique
    on public.storage_locations (location_source_code, detail_code)
    where detail_code is not null;
alter table public.found_items add column detail_status text;

create or replace function public.resolve_verified_detail_location()
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
            new.detail_status := null;
        elsif new.raw_storage_name = old.raw_storage_name and new.detail_checked_at is null then
            new.detail_org_id := old.detail_org_id;
            new.detail_org_name := old.detail_org_name;
            new.detail_department := old.detail_department;
            new.detail_phone := old.detail_phone;
            new.detail_checked_at := old.detail_checked_at;
            new.detail_status := old.detail_status;
        end if;
    end if;
    if new.location_match_status = 'ambiguous' and new.detail_status is not null then
        -- Reviewed rows use the CURRENT orgId only; never combine it with a historic place name.
        -- Unknown/non-held states must not fall through to a conflicting legacy rule.
        new.storage_location_id := null;
        new.location_match_status := 'unmatched';
        if new.detail_status = '보관중' then
            select l.id into target_id from public.storage_locations l
            where l.detail_code = new.detail_org_id and l.is_active
              and l.location_source_code = case new.item_source_code
                  when 'police_api' then 'esri_police' when 'partner_api' then 'partner_csv' end;
            if target_id is not null then
                new.storage_location_id := target_id;
                new.location_match_status := 'matched';
            end if;
        end if;
        return new;
    end if;
    -- Unreviewed rows retain the existing PRD 4.5 behavior.
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

-- PRD 4.5 — bounded manual-review batches, with identity/concurrency guards.
create function public.apply_reviewed_detail_evidence(evidence jsonb)
returns table (id bigint, storage_location_id bigint, location_match_status text)
language sql security invoker set search_path = '' as $$
    update public.found_items f set
        detail_org_id = e.org_id, detail_org_name = e.org_name,
        detail_department = e.department, detail_phone = e.phone,
        detail_status = e.status, detail_checked_at = e.checked_at,
        storage_location_id = null, location_match_status = 'ambiguous'
    from pg_catalog.jsonb_to_recordset(evidence) as e(
        id bigint, item_source_code text, atc_id text, found_sequence text,
        raw_storage_name text, previous_checked_at timestamptz,
        org_id text, org_name text, department text, phone text, status text,
        checked_at timestamptz)
    where f.id = e.id and f.item_source_code = e.item_source_code
      and f.atc_id = e.atc_id and f.found_sequence = e.found_sequence
      and f.raw_storage_name = e.raw_storage_name
      and f.detail_checked_at is not distinct from e.previous_checked_at
      and f.location_match_status in ('ambiguous', 'unmatched')
      and f.storage_location_id is null and e.checked_at is not null
      and e.org_id <> '' and e.status is not null
      and pg_catalog.jsonb_array_length(evidence) between 1 and 100
    returning f.id, f.storage_location_id, f.location_match_status;
$$;
revoke all on function public.apply_reviewed_detail_evidence(jsonb) from public, anon, authenticated;
grant execute on function public.apply_reviewed_detail_evidence(jsonb) to service_role;
notify pgrst, 'reload schema';
