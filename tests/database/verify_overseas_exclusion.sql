-- PRD 4.5, 6 — run after the overseas migration; all sample-row changes roll back.
begin;
set local lock_timeout = '5s';
set local statement_timeout = '60s';
do $test$
declare
    sample public.found_items%rowtype;
    rule record;
    incoming public.found_items%rowtype;
    result_count integer;
begin
    if not public.is_overseas_custody('partner_api',' 주 독일 대사관 ')
       or public.is_overseas_custody('police_api','주독일대사관')
       or public.is_overseas_custody('partner_api','주한독일대사관')
       or public.is_overseas_custody('partner_api','주독일대사관 서울안내소') then
        raise exception 'Overseas classification is not exact/source-scoped';
    end if;
    if has_function_privilege('anon','public.is_overseas_custody(text,text)','EXECUTE')
       or has_function_privilege('authenticated','public.exclude_overseas_custody()','EXECUTE') then
        raise exception 'Internal functions became public';
    end if;
    select * into strict sample from public.found_items
        where location_match_status='excluded_overseas' order by id limit 1;
    -- A reviewed partner rule need not exist after redundant-rule cleanup. Create an isolated
    -- rollback-only rule instead of relying on production evidence unrelated to this test.
    select 'partner_api'::text item_source_code,
        regexp_replace(lower(normalize(l.name,NFKC)),'[^[:alnum:]]','','g') normalized_raw_storage_name,
        'TEST-OVERSEAS-ROLLBACK-DOMESTIC'::text detail_org_id,l.id storage_location_id,l.name
        into strict rule from public.storage_locations l
        where l.is_active and l.location_source_code='partner_csv' order by l.id limit 1;
    insert into public.storage_location_detail_mappings
        (item_source_code,normalized_raw_storage_name,detail_org_id,storage_location_id,evidence_note)
        values(rule.item_source_code,rule.normalized_raw_storage_name,rule.detail_org_id,
            rule.storage_location_id,'Rollback-only overseas exclusion regression fixture');

    -- An older daily collector can send unmatched, ambiguous or an erroneous matched ID.
    update public.found_items set location_match_status='unmatched' where id=sample.id;
    if not exists(select 1 from public.found_items where id=sample.id
        and location_match_status='excluded_overseas' and storage_location_id is null)
        then raise exception 'Old unmatched input lost exclusion'; end if;
    update public.found_items set location_match_status='ambiguous' where id=sample.id;
    if (select location_match_status from public.found_items where id=sample.id)<>'excluded_overseas'
        then raise exception 'Old ambiguous input lost exclusion'; end if;
    update public.found_items set location_match_status='matched',storage_location_id=rule.storage_location_id
        where id=sample.id;
    if not exists(select 1 from public.found_items where id=sample.id
        and location_match_status='excluded_overseas' and storage_location_id is null)
        then raise exception 'Overseas item acquired a map location'; end if;

    -- The normal manual unresolved-evidence RPC must skip intentional exclusions.
    select count(*) into result_count from public.apply_reviewed_detail_evidence(
        jsonb_build_array(jsonb_build_object('id',sample.id,'item_source_code',sample.item_source_code,
        'atc_id',sample.atc_id,'found_sequence',sample.found_sequence,'raw_storage_name',sample.raw_storage_name,
        'previous_checked_at',sample.detail_checked_at,'org_id',rule.detail_org_id,
        'department',rule.normalized_raw_storage_name,'checked_at',clock_timestamp())));
    if result_count<>0 then raise exception 'Review RPC retried an excluded item'; end if;

    -- A newer domestic current place overrides the historical overseas raw name.
    update public.found_items set detail_department=rule.normalized_raw_storage_name,
        detail_org_id=rule.detail_org_id,detail_checked_at=clock_timestamp()
        where id=sample.id;
    if not exists(select 1 from public.found_items where id=sample.id
        and location_match_status='matched' and storage_location_id=rule.storage_location_id)
        then raise exception 'Domestic current evidence did not release exclusion'; end if;

    -- Real INSERT ON CONFLICT, including BEFORE INSERT and restored cached domestic evidence.
    select * into strict incoming from public.found_items where id=sample.id;
    incoming.location_match_status:='excluded_overseas';
    incoming.storage_location_id:=null;
    incoming.detail_org_id:=null;
    incoming.detail_department:=null;
    incoming.detail_checked_at:=null;
    insert into public.found_items overriding system value select incoming.*
        on conflict(item_source_code,atc_id,found_sequence) do update set
        location_match_status=excluded.location_match_status,storage_location_id=excluded.storage_location_id,
        detail_org_id=excluded.detail_org_id,detail_department=excluded.detail_department,
        detail_checked_at=excluded.detail_checked_at;
    if not exists(select 1 from public.found_items where id=sample.id
        and location_match_status='matched' and storage_location_id=rule.storage_location_id)
        then raise exception 'Re-ingestion overrode current domestic evidence'; end if;

    -- A raw domestic custody change clears stale foreign evidence under the existing resolver.
    update public.found_items set detail_department='주독일대사관',detail_org_id='TEST-FOREIGN',
        detail_checked_at=clock_timestamp() where id=sample.id;
    if (select location_match_status from public.found_items where id=sample.id)<>'excluded_overseas'
        then raise exception 'Current overseas evidence was ignored'; end if;
    update public.found_items set raw_storage_name=rule.name,location_match_status='matched',
        storage_location_id=rule.storage_location_id where id=sample.id;
    if not exists(select 1 from public.found_items where id=sample.id
        and location_match_status='matched' and detail_checked_at is null)
        then raise exception 'Domestic raw custody change stayed excluded'; end if;
end;
$test$;
rollback;
select 'overseas classification, permissions, old collector, review skip, current custody and real upsert passed; rolled back' as verification;
