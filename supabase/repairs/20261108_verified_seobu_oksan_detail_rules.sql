-- PRD 4.5, 6 — manually replay only after verifying current custody/phone.
-- Applied to production 2026-11-08. No item writes or name-only aliases.
begin;
do $repair$
begin
    if not exists(select 1 from public.storage_locations where id=1512
        and name='함평서부파출소' and phone='061-322-4112'
        and location_source_code='esri_police' and is_active)
        or not exists(select 1 from public.storage_locations where id=1187
        and name='청주흥덕옥산파출소' and phone='043-270-3708'
        and location_source_code='esri_police' and is_active)
    then raise exception 'Verified institution identity changed'; end if;
    insert into public.storage_location_detail_mappings
        (item_source_code,normalized_raw_storage_name,detail_org_id,storage_location_id,evidence_note)
    values
        ('police_api','서부파출소','O0001769',1512,
         '2026-11-08 Lost112 F2026110700000919/1,2: current Seobu O0001769, phone 061-322-4112 exactly matches Hampyeong Seobu institution 1512. Existing Ulsan O0000717 preserved.'),
        ('police_api','옥산파출소','O0002674',1187,
         '2026-11-08 Lost112 F2026110700000878/1: current Oksan O0002674, phone 043-270-3708 exactly matches Cheongju Heungdeok Oksan institution 1187. Existing O0001211 preserved; no global alias for ambiguous name.')
    on conflict (item_source_code,normalized_raw_storage_name,detail_org_id) do nothing;
    if not exists(select 1 from public.storage_location_detail_mappings
        where item_source_code='police_api' and normalized_raw_storage_name='서부파출소'
          and detail_org_id='O0001769' and storage_location_id=1512)
        or not exists(select 1 from public.storage_location_detail_mappings
        where item_source_code='police_api' and normalized_raw_storage_name='옥산파출소'
          and detail_org_id='O0002674' and storage_location_id=1187)
    then raise exception 'Conflicting current custody rule'; end if;
end;
$repair$;
commit;
