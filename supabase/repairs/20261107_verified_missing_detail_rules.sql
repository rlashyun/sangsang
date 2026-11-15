-- PRD 4.5, 6 — manual replay only. No item writes, schema changes or automatic seeding.
-- Fresh detail: F2026110600000480/1, Dongbu police box, O0001326, 041-689-9717.
-- Official parent/address/phone: https://cnpolice.go.kr/SEO/SS/main.htm?Gubun=V01_04
-- Same official Hongcheon-ro 20 address as the existing institution. No name-only global alias.
-- Additional full-sync finding: Cheomdan O0000656; detail phone 062-971-5112
-- exactly matches the existing master and current Kakao POI 10852737, same road address.
begin;
do $repair$
declare target_id bigint;
begin
    select id into strict target_id from public.storage_locations
    where location_source_code='esri_police' and source_key='substation:1044'
      and name='서산동부파출소' and is_active and address like '%홍천로 20%';
    insert into public.storage_location_detail_mappings
        (item_source_code,normalized_raw_storage_name,detail_org_id,storage_location_id,evidence_note)
    values ('police_api','동부파출소','O0001326',target_id,
        '2026-11-07 fresh Lost112 current custody and code O0001326; phone 041-689-9717 exactly matches official Seosan Dongbu police box; official Hongcheon-ro 20 address matches existing substation:1044. https://cnpolice.go.kr/SEO/SS/main.htm?Gubun=V01_04')
    on conflict (item_source_code,normalized_raw_storage_name,detail_org_id) do nothing;
    if not exists(select 1 from public.storage_location_detail_mappings
        where item_source_code='police_api' and normalized_raw_storage_name='동부파출소'
          and detail_org_id='O0001326' and storage_location_id=target_id)
        then raise exception 'Conflicting current custody rule; abort'; end if;
end;
$repair$;
do $repair$
declare target_id bigint;
begin
    select id into strict target_id from public.storage_locations
    where location_source_code='esri_police' and source_key='substation:498'
      and name='광주광산첨단지구대' and is_active and phone='062-971-5112';
    insert into public.storage_location_detail_mappings
        (item_source_code,normalized_raw_storage_name,detail_org_id,storage_location_id,evidence_note)
    values ('police_api','첨단지구대','O0000656',target_id,
        '2026-11-07 fresh Lost112 F2026110600003233/1: current Cheomdan precinct O0000656, phone 062-971-5112; exact existing Gwangju Gwangsan master phone and road address match current Kakao POI 10852737. New Songdo institution makes raw Cheomdan ambiguous; no global name-only alias.')
    on conflict (item_source_code,normalized_raw_storage_name,detail_org_id) do nothing;
    if not exists(select 1 from public.storage_location_detail_mappings
        where item_source_code='police_api' and normalized_raw_storage_name='첨단지구대'
          and detail_org_id='O0000656' and storage_location_id=target_id)
        then raise exception 'Conflicting current custody rule; abort'; end if;
end;
$repair$;
commit;
