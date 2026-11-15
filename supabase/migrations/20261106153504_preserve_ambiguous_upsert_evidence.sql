-- PRD 4.5 — preserve the ambiguity marker through a real INSERT ON CONFLICT.
-- PRD 4.5, 6 — 2026-11-07 user decision: one table, current custody, status ignored.
create or replace function public.resolve_verified_detail_location()
returns trigger language plpgsql security invoker set search_path = '' as $$
declare
    target_id bigint;
    fresh_detail boolean := new.detail_checked_at is not null;
begin
    if tg_op = 'UPDATE' then
        fresh_detail := new.detail_checked_at is not null
            and new.detail_checked_at is distinct from old.detail_checked_at;
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
    if new.location_match_status = 'ambiguous' or fresh_detail then
        -- Same source/current place/code key as the ingestion dictionary. No raw-name fallback.
        select m.storage_location_id into target_id
        from public.storage_location_detail_mappings m
        join public.storage_locations l on l.id = m.storage_location_id and l.is_active
        where m.item_source_code = new.item_source_code
          and m.normalized_raw_storage_name = pg_catalog.regexp_replace(
              pg_catalog.lower(pg_catalog.normalize(new.detail_department, 'NFKC')),
              '[^[:alnum:]]', '', 'g')
          and m.detail_org_id = new.detail_org_id
          and new.detail_checked_at is not null
          and l.location_source_code = case new.item_source_code
              when 'police_api' then 'esri_police' when 'partner_api' then 'partner_csv' end;
        new.storage_location_id := target_id;
        if target_id is not null then
            new.location_match_status := 'matched';
        elsif tg_op = 'INSERT' and not fresh_detail then
            -- BEFORE INSERT runs before ON CONFLICT UPDATE. Preserve the internal
            -- ambiguous marker so UPDATE can restore and match cached evidence.
            -- The ingestion store finalizes a genuinely new unresolved row afterwards.
            new.location_match_status := 'ambiguous';
        else
            new.location_match_status := 'unmatched';
        end if;
    end if;
    return new;
end;
$$;

notify pgrst, 'reload schema';
