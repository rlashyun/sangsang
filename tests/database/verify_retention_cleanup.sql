-- PRD 4.5, 5 — positive deletion, source isolation, cap and KST boundary; all rolled back.
begin;
set local statement_timeout='60s';
do $test$
declare sample public.found_items%rowtype; i integer; removed bigint; cutoff date;
begin
    if has_function_privilege('anon','public.delete_expired_found_items(text,integer)','EXECUTE')
        then raise exception 'Retention function became public'; end if;
    select * into strict sample from public.found_items order by id limit 1;
    for i in 0..4 loop
        sample.id:=-9223372036854770000-i;
        sample.atc_id:='RETRIEVER-RETENTION-ROLLBACK-'||i;
        sample.found_sequence:='1';
        sample.item_source_code:=case when i in (2,4) then 'partner_api' else 'police_api' end;
        select ((now() at time zone 'Asia/Seoul')::date-make_interval(months=>s.retention_months::integer))::date
            into strict cutoff from public.item_sources s where s.code=sample.item_source_code and s.enabled;
        sample.registered_on:=case when i<3 then date '1900-01-01'+i else cutoff end;
        sample.raw_storage_name:='RETENTION-ROLLBACK-FIXTURE';
        sample.storage_location_id:=null;sample.location_match_status:='unmatched';
        sample.detail_org_id:=null;sample.detail_org_name:=null;sample.detail_department:=null;
        sample.detail_phone:=null;sample.detail_status:=null;sample.detail_checked_at:=null;
        insert into public.found_items overriding system value select sample.*;
    end loop;
    select sum(deleted_count) into removed from public.delete_expired_found_items(null,0);
    if removed<>0 then raise exception 'Zero cap deleted rows'; end if;
    select sum(deleted_count) into removed from public.delete_expired_found_items('police_api',1);
    if removed<>1 or (select count(*) from public.found_items where id in (-9223372036854770000,-9223372036854770001))<>1
        then raise exception 'Batch cap or expired deletion failed'; end if;
    if not exists(select 1 from public.found_items where id=-9223372036854770002)
        then raise exception 'Police cleanup deleted partner fixture'; end if;
    select sum(deleted_count) into removed from public.delete_expired_found_items('police_api',1);
    if removed<>1 then raise exception 'Second expiry batch failed'; end if;
    select sum(deleted_count) into removed from public.delete_expired_found_items('partner_api',1);
    if removed<>1 then raise exception 'Partner expiry batch failed'; end if;
    if (select count(*) from public.found_items where id in (-9223372036854770003,-9223372036854770004))<>2
        then raise exception 'Inclusive retention boundary was deleted'; end if;
end;
$test$;
rollback;
select 'expiry deletion, source isolation, batch limit, KST boundary and permissions passed; rolled back' verification;
