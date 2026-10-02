-- PRD 4.5: run after the migrations and evidence backfill; all changes roll back.
begin;
do $test$
declare
    original public.found_items%rowtype;
begin
    select f.* into strict original
    from public.found_items f
    join public.storage_location_detail_mappings m
      on m.item_source_code=f.item_source_code and m.detail_org_id=f.detail_org_id
     and m.normalized_raw_storage_name=regexp_replace(
         lower(normalize(f.raw_storage_name,NFKC)), '[^[:alnum:]]', '', 'g')
     and m.storage_location_id=f.storage_location_id
    where f.location_match_status='matched'
    order by f.id limit 1;

    -- Reproduce an upsert that omits or clears cached evidence.
    insert into public.found_items
      (id,item_source_code,atc_id,found_sequence,category_id,raw_category_name,
       registered_on,raw_storage_name,storage_location_id,location_match_status)
    overriding system value
    values (original.id,original.item_source_code,original.atc_id,original.found_sequence,
            original.category_id,original.raw_category_name,original.registered_on,
            original.raw_storage_name,null,'ambiguous')
    on conflict (item_source_code,atc_id,found_sequence) do update
    set storage_location_id=excluded.storage_location_id,
        location_match_status=excluded.location_match_status,
        detail_org_id=excluded.detail_org_id,detail_checked_at=excluded.detail_checked_at;
    if (select storage_location_id from public.found_items where id=original.id)
       is distinct from original.storage_location_id then
        raise exception 'Re-ingestion lost a verified match';
    end if;

    update public.storage_locations set is_active=false where id=original.storage_location_id;
    update public.found_items set storage_location_id=null,location_match_status='ambiguous'
    where id=original.id;
    if (select storage_location_id from public.found_items where id=original.id) is not null then
        raise exception 'Inactive institution was accepted';
    end if;
    update public.storage_locations set is_active=true where id=original.storage_location_id;
    update public.found_items set location_match_status='ambiguous' where id=original.id;
    if (select storage_location_id from public.found_items where id=original.id)
       is distinct from original.storage_location_id then
        raise exception 'Verified rule was not applied';
    end if;

    update public.found_items set detail_org_id='UNVERIFIED',detail_checked_at=clock_timestamp(),
      storage_location_id=null,location_match_status='ambiguous' where id=original.id;
    if (select storage_location_id from public.found_items where id=original.id) is not null then
        raise exception 'Unknown institution code was accepted';
    end if;
    update public.found_items set raw_storage_name='검증하지 않은 보관장소' where id=original.id;
    if (select detail_org_id from public.found_items where id=original.id) is not null then
        raise exception 'Changed place retained stale evidence';
    end if;
end;
$test$;
select 'Verified match, real upsert, inactive institution, unknown code, changed place: PASS' as result;
rollback;
