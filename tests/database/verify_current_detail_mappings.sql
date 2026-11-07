-- PRD 4.5, 6 — execute after the unified mapping migration; all changes roll back.
begin;
do $test$
declare
    original public.found_items%rowtype;
    institution public.storage_locations%rowtype;
    result_id bigint;
begin
    select f.* into strict original from public.found_items f
    join public.storage_location_detail_mappings m on m.item_source_code = f.item_source_code
      and m.normalized_raw_storage_name = regexp_replace(lower(normalize(f.detail_department,NFKC)), '[^[:alnum:]]', '', 'g')
      and m.detail_org_id = f.detail_org_id and m.storage_location_id = f.storage_location_id
    join public.storage_locations l on l.id = f.storage_location_id and l.is_active
    order by f.id limit 1;
    select * into strict institution from public.storage_locations where id = original.storage_location_id;

    -- A re-ingestion that supplies no detail fields retains the reviewed evidence.
    update public.found_items set detail_org_id = null, detail_checked_at = null,
        detail_status = null, storage_location_id = null, location_match_status = 'ambiguous'
    where id = original.id;
    select storage_location_id into result_id from public.found_items where id = original.id;
    if result_id is distinct from institution.id then raise exception 'Re-ingestion lost reviewed match'; end if;

    update public.found_items set detail_status = '종결', detail_checked_at = clock_timestamp(),
        storage_location_id = null, location_match_status = 'ambiguous' where id = original.id;
    if (select storage_location_id from public.found_items where id = original.id) is distinct from institution.id
        then raise exception 'Treatment status incorrectly blocked matching'; end if;

    update public.found_items set detail_status = '삭제', detail_checked_at = clock_timestamp(),
        storage_location_id = null, location_match_status = 'ambiguous' where id = original.id;
    if (select storage_location_id from public.found_items where id = original.id) is distinct from institution.id
        then raise exception 'Deleted status incorrectly blocked matching'; end if;

    update public.found_items set detail_status = '반입중', detail_checked_at = clock_timestamp(),
        storage_location_id = null, location_match_status = 'ambiguous' where id = original.id;
    if (select storage_location_id from public.found_items where id = original.id) is distinct from institution.id
        then raise exception 'Inbound status incorrectly blocked matching'; end if;

    update public.found_items set detail_status = '보관중', detail_org_id = 'UNVERIFIED',
        detail_checked_at = clock_timestamp(), storage_location_id = null,
        location_match_status = 'ambiguous' where id = original.id;
    if (select storage_location_id from public.found_items where id = original.id) is not null
        then raise exception 'Unknown code matched'; end if;

    update public.storage_locations set is_active = false where id = institution.id;
    update public.found_items set detail_org_id = original.detail_org_id,
        detail_checked_at = clock_timestamp(), storage_location_id = null,
        location_match_status = 'ambiguous' where id = original.id;
    if (select storage_location_id from public.found_items where id = original.id) is not null
        then raise exception 'Inactive institution matched'; end if;
    update public.storage_locations set is_active = true where id = institution.id;

    update public.found_items set raw_storage_name = 'changed place', storage_location_id = null,
        location_match_status = 'ambiguous' where id = original.id;
    if (select detail_status from public.found_items where id = original.id) is not null
        then raise exception 'Changed place retained stale status'; end if;
    if (select raw_payload from public.found_items where id = original.id) is distinct from original.raw_payload
        then raise exception 'Raw payload was modified'; end if;
    if has_function_privilege('anon','public.apply_reviewed_detail_evidence(jsonb)','execute')
       or has_function_privilege('authenticated','public.apply_reviewed_detail_evidence(jsonb)','execute')
       then raise exception 'Public access to review RPC'; end if;
end;
$test$;
select 'Unified current-place mapping, status ignored: PASS' as result;
rollback;
