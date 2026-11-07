-- PRD 4.5, 6 — manual replay of reviewed 47 institutions and 95 aliases.
-- Uses stable source keys. Does not modify existing institutions, item rows or detail rules.
-- The redundant 79 detail rules were removed and are intentionally absent.
-- Review evidence: docs/measurements.md, no-name-candidate repair 2026-11-07.
begin;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'esri_police','manual:lost112:O0000545','인천국제공항경찰단','인천국제공항경찰단','police','인천 영종구 공항로 272','182','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E6100000D54EA3B7AE9C5F40FA27781924B94240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='esri_police' and source_key='manual:lost112:O0000545')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'esri_police','manual:lost112:O0003304','울산북부농소2파출소','울산북부농소2파출소','police','울산 북구 호계로 391','052-297-0112','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E6100000956FD388192B60409BD6B52595D14140','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='esri_police' and source_key='manual:lost112:O0003304')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'esri_police','manual:lost112:O0002818','서울지하철경찰대(수사4신도림)','서울지하철경찰대수사4신도림','police','서울 구로구 새말로 지하 117-21','02-700-2950','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E610000052E3827607B95F40AEEE4E611FC14240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='esri_police' and source_key='manual:lost112:O0002818')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'esri_police','manual:lost112:O0000287','부산동래온천1파출소','부산동래온천1파출소','police','부산 동래구 금강로151번길 9','051-555-1100','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E61000000747EABC93226040EE4A02D7719C4140','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='esri_police' and source_key='manual:lost112:O0000287')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'esri_police','manual:lost112:O0000021','서울종로교남파출소','서울종로교남파출소','police','서울 종로구 송월길 152','02-3701-4310','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E61000007FBBE5AB94BD5F405602F52F31C94240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='esri_police' and source_key='manual:lost112:O0000021')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'esri_police','manual:lost112:O0000001','경찰청(본청)','경찰청본청','police','서울 서대문구 통일로 97','182','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E61000009B364385ECBD5F405DFD0D6C1DC84240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='esri_police' and source_key='manual:lost112:O0000001')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'esri_police','manual:lost112:O0003017','경기북부 기동순찰1대','경기북부기동순찰1대','police','경기 의정부시 금오로23번길 22-49','031-961-3246','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E6100000A3FB755674C45F408A3B224BE0E04240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='esri_police' and source_key='manual:lost112:O0003017')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'esri_police','manual:lost112:O0003340','청주흥덕송절파출소','청주흥덕송절파출소','police','충북 청주시 흥덕구 송화로 85','043-270-3711','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E61000004BC2A55B65DC5F409485CB7CD7544240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='esri_police' and source_key='manual:lost112:O0003340')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'esri_police','manual:lost112:O0003349','세종남부집현파출소','세종남부집현파출소','police','세종특별자치시 남세종로 37','044-320-8106','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E6100000552430D8D9D45F406A7488B0663F4240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='esri_police' and source_key='manual:lost112:O0003349')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'esri_police','manual:lost112:O0003352','화성서부새솔파출소','화성서부새솔파출소','police','경기 화성시 만세구 수노을2로 17','031-5183-1812','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E61000006C4AEB1F3EB45F403A249C086DA44240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='esri_police' and source_key='manual:lost112:O0003352')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'esri_police','manual:lost112:O0002830','서울지하철경찰대(수사3잠실)','서울지하철경찰대수사3잠실','police','서울 송파구 올림픽로 305','02-700-2944','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E61000000DE92C7BABC65F4047339AC5EAC14240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='esri_police' and source_key='manual:lost112:O0002830')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'esri_police','manual:lost112:O0003188','세종특별자치시경찰청','세종특별자치시경찰청','police','세종특별자치시 한누리대로 1978','044-599-2247','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E6100000276D9E224DD35F4082EF1586723E4240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='esri_police' and source_key='manual:lost112:O0003188')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'esri_police','manual:lost112:O0000356','부산남부대연4치안센터','부산남부대연4치안센터','police','부산 남구 유엔로 152','051-624-0620','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E6100000F5B8CD62F922604049E9EBA2A2904140','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='esri_police' and source_key='manual:lost112:O0000356')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'esri_police','manual:lost112:O0003154','서울구로구로1치안센터','서울구로구로1치안센터','police','서울 구로구 구일로 52','02-852-0112','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E6100000E1B350E102B85F40FDF9373EDEBE4240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='esri_police' and source_key='manual:lost112:O0003154')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'esri_police','manual:lost112:O0000275','부산중부창선치안센터','부산중부창선치안센터','police','부산 중구 광복중앙로 1','051-245-0831','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E61000002854C150FF2060404CC0DB7BBB8C4140','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='esri_police' and source_key='manual:lost112:O0000275')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'esri_police','manual:lost112:O0003348','청주상당방서지구대','청주상당방서지구대','police','충북 청주시 상당구 월운로 30','043-280-1709','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E6100000BB591FCBB0E05F400BD5A01BE64C4240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='esri_police' and source_key='manual:lost112:O0003348')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'esri_police','manual:lost112:O0002781','대전유성전민치안센터','대전유성전민치안센터','police','대전 유성구 엑스포로466번길 55','042-725-6512','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E6100000B2C0F730FED95F403E5038FD16334240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='esri_police' and source_key='manual:lost112:O0002781')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'esri_police','manual:lost112:O0000430','부산연제연산5치안센터','부산연제연산5치안센터','police','부산 연제구 월드컵대로165번길 70','051-852-0112','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E6100000D24183A870226040F12CF6D59C974140','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='esri_police' and source_key='manual:lost112:O0000430')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'esri_police','manual:lost112:O0003168','대전중부석교치안센터','대전중부석교치안센터','police','대전 중구 석교동 70-15','042-271-9381','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E61000001D86BC0C8ADC5F406FF1BD7A69274240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='esri_police' and source_key='manual:lost112:O0003168')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'esri_police','manual:lost112:O0001073','강원도경찰청','강원도경찰청','police','강원특별자치도 춘천시 동내면 세실로 49','033-248-0547','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E6100000E5324A7B77F05F40A236C18B52EE4240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='esri_police' and source_key='manual:lost112:O0001073')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'esri_police','manual:lost112:O0002677','제주서부제주공항경찰대','제주서부제주공항경찰대','police','제주특별자치도 제주시 공항로 2','064-798-3811','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E6100000C0C51FAFBD9F5F400C45F847C2C04040','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='esri_police' and source_key='manual:lost112:O0002677')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'esri_police','manual:lost112:O0003353','인천연수송도달빛지구대','인천연수송도달빛지구대','police','인천 연수구 인천타워대로495번길 77','032-453-0870','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E6100000F9C96C6AA0A75F404F1F71325EB44240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='esri_police' and source_key='manual:lost112:O0003353')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'esri_police','manual:lost112:O0002807','인천연수송도첨단지구대','인천연수송도첨단지구대','police','인천 연수구 송도과학로27번길 70','032-453-0840','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E6100000E0F19A57B4AA5F40E954FE5AA9B14240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='esri_police' and source_key='manual:lost112:O0002807')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'partner_csv','manual:lost112:V0007334','CGV(동대문)','cgv동대문','partner','서울 중구 장충단로13길 20','02-2136-3501','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E61000007B2539F67DC05F40B3860CF5CBC84240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='partner_csv' and source_key='manual:lost112:V0007334')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'partner_csv','manual:lost112:V0007721','CGV(목포평화광장)','cgv목포평화광장','partner','전남광주통합특별시 목포시 원형동로 53','061-270-0244','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E6100000F80E281DBB9B5F408F6CBB664D664140','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='partner_csv' and source_key='manual:lost112:V0007721')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'partner_csv','manual:lost112:V0007730','CJ CGV 대전가오','cjcgv대전가오','partner','대전 동구 은어송로 72','042-2869-9542','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E610000096BF554D50DD5F40F246E4924C274240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='partner_csv' and source_key='manual:lost112:V0007730')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'partner_csv','manual:lost112:V0002940','경북본부(한국철도공사)','경북본부한국철도공사','partner','경북 영주시 시청로2번길 30','054-639-2077','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E61000002FCED5600B146040AAA4C79535674240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='partner_csv' and source_key='manual:lost112:V0002940')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'partner_csv','manual:lost112:V0007722','광남자동차(주)','광남자동차주','partner','대구 동구 팔공로 400','053-985-5006','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E6100000E7FCA4AC9C146040FAAC159CA2F74140','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='partner_csv' and source_key='manual:lost112:V0007722')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'partner_csv','manual:lost112:V0007715','광주청년센터','광주청년센터','partner','전남광주통합특별시 동구 예술길 38','000-0000-0000','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E6100000FEBC6091D9BA5F4022706A680C934140','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='partner_csv' and source_key='manual:lost112:V0007715')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'partner_csv','manual:lost112:V0000780','다모아자동차','다모아자동차','partner','서울 마포구 가양대로 117','02-376-2700','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E6100000FBADDD3626B85F402673F77260CA4240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='partner_csv' and source_key='manual:lost112:V0000780')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'partner_csv','manual:lost112:V0007738','다이소(청담도산공원점)','다이소청담도산공원점','partner','서울 강남구 도산대로49길 9','000-0000-0000','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E6100000437E285F5FC25F40FC65FDF3FDC24240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='partner_csv' and source_key='manual:lost112:V0007738')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'partner_csv','manual:lost112:V0007742','다이소(포이사거리점)','다이소포이사거리점','partner','서울 서초구 논현로 79','000-1522-4400','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E6100000F430E8C8DDC25F408B1A07A10BBD4240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='partner_csv' and source_key='manual:lost112:V0007742')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'partner_csv','manual:lost112:V0007670','다이소(포항성곡점)','다이소포항성곡점','partner','경북 포항시 북구 흥해읍 소티재로 350','000-1522-4400','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E6100000605DC255FA2A6040DE776ADD3F0A4240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='partner_csv' and source_key='manual:lost112:V0007670')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'partner_csv','manual:lost112:V0007692','덕수궁관리소','덕수궁관리소','partner','서울 중구 세종대로 99','02-751-0731','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E61000000F6EF8AC64BE5F4032049D6564C84240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='partner_csv' and source_key='manual:lost112:V0007692')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'partner_csv','manual:lost112:V0007713','목동실내빙상장','목동실내빙상장','partner','서울 양천구 안양천로 939','070-5168-6879','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E6100000AAB316D145B85F4080C51813EFC34240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='partner_csv' and source_key='manual:lost112:V0007713')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'partner_csv','manual:lost112:V0007716','부산시설공단 스포원','부산시설공단스포원','partner','부산 금정구 체육공원로399번길 324','051-550-1429','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E61000005E48A141592360403C56FBCC1CA54140','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='partner_csv' and source_key='manual:lost112:V0007716')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'partner_csv','manual:lost112:V0007725','빛고을국민안전체험관','빛고을국민안전체험관','partner','전남광주통합특별시 북구 능안로30번길 3','062-0000-0000','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E610000085D6D1A0ABBA5F40259DC68026984140','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='partner_csv' and source_key='manual:lost112:V0007725')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'partner_csv','manual:lost112:V0007698','삼성전자 DSR연구동','삼성전자dsr연구동','partner','경기 화성시 병점구 삼성전자로 1-1','031-8096-0221','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E610000022D4BE9180C45F40EFDFD9DDD79C4240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='partner_csv' and source_key='manual:lost112:V0007698')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'partner_csv','manual:lost112:V0007737','신라면세점 서울점','신라면세점서울점','partner','서울 중구 동호로 249','02-2230-3023','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E61000006CD19EA57EC05F4046C6D2CF50C74240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='partner_csv' and source_key='manual:lost112:V0007737')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'partner_csv','manual:lost112:V0007727','올리브영(영종하늘도시점)','올리브영영종하늘도시점','partner','인천 영종구 하늘중앙로 185','010-7231-2645','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E6100000BC53768AECA35F40D9344D4AD3BE4240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='partner_csv' and source_key='manual:lost112:V0007727')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'partner_csv','manual:lost112:V0007680','웨스틴 서울 파르나스호텔','웨스틴서울파르나스호텔','partner','서울 강남구 봉은사로 524','010-5790-8574','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E6100000F2A7749DAAC35F4035D8206DA2C14240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='partner_csv' and source_key='manual:lost112:V0007680')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'partner_csv','manual:lost112:V0007717','유니클로(디타워점)','유니클로디타워점','partner','서울 종로구 종로3길 17','02-2251-8202','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E6100000F572CA31A9BE5F40669417DE07C94240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='partner_csv' and source_key='manual:lost112:V0007717')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'partner_csv','manual:lost112:V0007751','인하대학교 학생지원팀','인하대학교학생지원팀','partner','인천 미추홀구 인하로 100','032-860-7065','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E61000005FE92CF406AA5F40BEBB5C0689B94240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='partner_csv' and source_key='manual:lost112:V0007751')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'partner_csv','manual:lost112:V0003880','진접차량사업소','진접차량사업소','partner','경기 남양주시 진접읍 금곡리 산 40-35','02-6110-7870','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E61000000FF32D263FCE5F40A2A2A62EEBDB4240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='partner_csv' and source_key='manual:lost112:V0003880')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'partner_csv','manual:lost112:V0007714','팽성 청담 스포츠센터','팽성청담스포츠센터','partner','경기 평택시 팽성읍 팽성송화로 204','031-618-3471','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E6100000D090E09DFCC35F402E865724537B4240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='partner_csv' and source_key='manual:lost112:V0007714')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'partner_csv','manual:lost112:V0004100','한성대학교','한성대학교','partner','서울 성북구 삼선교로16길 116','02-760-8091','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E61000005CB5F488A7C05F40F1D3236891CA4240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='partner_csv' and source_key='manual:lost112:V0004100')
on conflict (location_source_code,source_key) do nothing;
insert into public.storage_locations (location_source_code,source_key,name,normalized_name,location_kind,address,phone,region_code,is_active,position)
select 'partner_csv','manual:lost112:V0007720','인천교통공사 송도파트','인천교통공사송도파트','partner','인천 연수구 아카데미로51번길 42','0507-1327-1452','',true,extensions.st_geogfromwkb(pg_catalog.decode('0101000020E61000007ABBA9AADCA85F40CD74838785AF4240','hex'))
where not exists (select 1 from public.storage_locations where location_source_code='partner_csv' and source_key='manual:lost112:V0007720')
on conflict (location_source_code,source_key) do nothing;
do $aliases$
declare reviewed record; target_id bigint;
begin
for reviewed in select * from (values
('police_api','인천국제공항경찰단','인천국제공항경찰단','','esri_police','manual:lost112:O0000545'),
('police_api','하대파출소','하대파출소','','esri_police','substation:1565'),
('police_api','인천부평경찰서민원봉사실','인천부평경찰서민원봉사실','','esri_police','station:61'),
('police_api','노원경찰서민원봉사실','노원경찰서민원봉사실','','esri_police','station:27'),
('police_api','남대문경찰서민원봉사실','남대문경찰서민원봉사실','','esri_police','station:3'),
('police_api','광명경찰서민원봉사실','광명경찰서민원봉사실','','esri_police','station:98'),
('police_api','부산중부경찰서민원실','부산중부경찰서민원실','','esri_police','station:32'),
('police_api','농소2파출소','농소2파출소','','esri_police','manual:lost112:O0003304'),
('police_api','서울강북경찰서민원봉사실','서울강북경찰서민원봉사실','','esri_police','station:15'),
('police_api','서울노원경찰서교통정보센터','서울노원경찰서교통정보센터','','esri_police','station:27'),
('police_api','서울지하철경찰대(수사4신도림)','서울지하철경찰대수사4신도림','','esri_police','manual:lost112:O0002818'),
('police_api','구로경찰서민원봉사실','구로경찰서민원봉사실','','esri_police','station:23'),
('police_api','석문파출소','석문파출소','','esri_police','substation:1062'),
('police_api','온천1파출소','온천1파출소','','esri_police','manual:lost112:O0000287'),
('police_api','서울영등포경찰서교통정보센터','서울영등포경찰서교통정보센터','','esri_police','station:10'),
('police_api','분당경찰서민원봉사실','분당경찰서민원봉사실','','esri_police','station:94'),
('police_api','서울중부경찰서민원봉사실','서울중부경찰서민원봉사실','','esri_police','station:1'),
('police_api','교남파출소','교남파출소','','esri_police','manual:lost112:O0000021'),
('police_api','일산동부경찰서민원봉사실','일산동부경찰서민원봉사실','','esri_police','station:119'),
('police_api','경찰청(본청)','경찰청본청','','esri_police','manual:lost112:O0000001'),
('police_api','삼척경찰서민원봉사실','삼척경찰서민원봉사실','','esri_police','station:135'),
('police_api','종암경찰서민원봉사실','종암경찰서민원봉사실','','esri_police','station:22'),
('police_api','부천원미경찰서민원봉사실','부천원미경찰서민원봉사실','','esri_police','station:96'),
('police_api','동래경찰서민원봉사실','동래경찰서민원봉사실','','esri_police','station:33'),
('police_api','강릉경찰서민원봉사실','강릉경찰서민원봉사실','','esri_police','station:130'),
('police_api','고양경찰서민원봉사실','고양경찰서민원봉사실','','esri_police','station:118'),
('police_api','서울중랑경찰서교통정보센터','서울중랑경찰서교통정보센터','','esri_police','station:17'),
('police_api','서울종암경찰서교통정보센터','서울종암경찰서교통정보센터','','esri_police','station:22'),
('police_api','서울동대문경찰서교통정보센터','서울동대문경찰서교통정보센터','','esri_police','station:8'),
('police_api','서울강서경찰서 교통정보센터','서울강서경찰서교통정보센터','','esri_police','station:20'),
('police_api','서울서부경찰서민원봉사실','서울서부경찰서민원봉사실','','esri_police','station:14'),
('police_api','양천경찰서민원봉사실','양천경찰서민원봉사실','','esri_police','station:25'),
('police_api','경기북부 기동순찰1대','경기북부기동순찰1대','','esri_police','manual:lost112:O0003017'),
('police_api','인천영종경찰서민원봉사실','인천영종경찰서민원봉사실','','esri_police','manual:stale:인천영종경찰서'),
('police_api','내리파출소','내리파출소','','esri_police','substation:817'),
('police_api','서울강서경찰서민원봉사실','서울강서경찰서민원봉사실','','esri_police','station:20'),
('police_api','충북보은경찰서민원봉사실','충북보은경찰서민원봉사실','','esri_police','station:154'),
('police_api','병점지구대','병점지구대','','esri_police','substation:835'),
('police_api','송절파출소','송절파출소','','esri_police','manual:lost112:O0003340'),
('police_api','집현파출소','집현파출소','','esri_police','manual:lost112:O0003349'),
('police_api','새솔파출소','새솔파출소','','esri_police','manual:lost112:O0003352'),
('police_api','군포경찰서민원봉사실','군포경찰서민원봉사실','','esri_police','station:91'),
('police_api','서울지하철경찰대(수사3잠실)','서울지하철경찰대수사3잠실','','esri_police','manual:lost112:O0002830'),
('police_api','나주경찰서민원봉사실','나주경찰서민원봉사실','','esri_police','station:191'),
('police_api','인천삼산경찰서민원봉사실','인천삼산경찰서민원봉사실','','esri_police','station:62'),
('police_api','세종특별자치시경찰청','세종특별자치시경찰청','','esri_police','manual:lost112:O0003188'),
('police_api','대연4치안센터','대연4치안센터','','esri_police','manual:lost112:O0000356'),
('police_api','금천경찰서민원봉사실','금천경찰서민원봉사실','','esri_police','station:16'),
('police_api','구로1치안센터','구로1치안센터','','esri_police','manual:lost112:O0003154'),
('police_api','해운대경찰서민원봉사실','해운대경찰서민원봉사실','','esri_police','station:39'),
('police_api','서울마포경찰서교통정보센터','서울마포경찰서교통정보센터','','esri_police','station:9'),
('police_api','청도경찰서민원봉사실','청도경찰서민원봉사실','','esri_police','station:223'),
('police_api','문경경찰서민원봉사실','문경경찰서민원봉사실','','esri_police','station:220'),
('police_api','창선치안센터','창선치안센터','','esri_police','manual:lost112:O0000275'),
('police_api','방서지구대','방서지구대','','esri_police','manual:lost112:O0003348'),
('police_api','전민치안센터','전민치안센터','','esri_police','manual:lost112:O0002781'),
('police_api','연산5치안센터','연산5치안센터','','esri_police','manual:lost112:O0000430'),
('police_api','서울성동경찰서교통정보센터','서울성동경찰서교통정보센터','','esri_police','station:11'),
('police_api','석교치안센터','석교치안센터','','esri_police','manual:lost112:O0003168'),
('police_api','부산동부경찰서민원봉사실','부산동부경찰서민원봉사실','','esri_police','station:35'),
('police_api','서대문경찰서민원봉사실','서대문경찰서민원봉사실','','esri_police','station:4'),
('police_api','강원도경찰청','강원도경찰청','','esri_police','manual:lost112:O0001073'),
('police_api','서울도봉경찰서민원봉사실','서울도봉경찰서민원봉사실','','esri_police','station:30'),
('police_api','제주공항경찰대','제주공항경찰대','','esri_police','manual:lost112:O0002677'),
('police_api','소요공동체지역관서','소요공동체지역관서','','esri_police','substation:676'),
('police_api','송도달빛지구대','송도달빛지구대','','esri_police','manual:lost112:O0003353'),
('police_api','송도첨단지구대','송도첨단지구대','','esri_police','manual:lost112:O0002807'),
('police_api','원주경찰서민원봉사실','원주경찰서민원봉사실','','esri_police','station:131'),
('partner_api','CGV(동대문)','cgv동대문','','partner_csv','manual:lost112:V0007334'),
('partner_api','CGV(목포평화광장)','cgv목포평화광장','','partner_csv','manual:lost112:V0007721'),
('partner_api','CJ CGV 대전가오','cjcgv대전가오','','partner_csv','manual:lost112:V0007730'),
('partner_api','경북본부(한국철도공사)','경북본부한국철도공사','','partner_csv','manual:lost112:V0002940'),
('partner_api','광남자동차(주)','광남자동차주','','partner_csv','manual:lost112:V0007722'),
('partner_api','광주청년센터','광주청년센터','','partner_csv','manual:lost112:V0007715'),
('partner_api','남성교통','남성교통','','partner_csv','partner:c43b9717ed393a2e73ef'),
('partner_api','다모아자동차','다모아자동차','','partner_csv','manual:lost112:V0000780'),
('partner_api','다이소 청당도산공원점','다이소청당도산공원점','','partner_csv','manual:lost112:V0007738'),
('partner_api','다이소(민락점)','다이소민락점','','partner_csv','partner:b064f84bd2ff92bce5c7'),
('partner_api','다이소(포이사거리점)','다이소포이사거리점','','partner_csv','manual:lost112:V0007742'),
('partner_api','다이소(포항성곡점)','다이소포항성곡점','','partner_csv','manual:lost112:V0007670'),
('partner_api','덕수궁관리소','덕수궁관리소','','partner_csv','manual:lost112:V0007692'),
('partner_api','목동실내빙상장','목동실내빙상장','','partner_csv','manual:lost112:V0007713'),
('partner_api','부산시설공단 스포원','부산시설공단스포원','','partner_csv','manual:lost112:V0007716'),
('partner_api','빛고을국민안전체험관','빛고을국민안전체험관','','partner_csv','manual:lost112:V0007725'),
('partner_api','삼성전자 DSR연구동','삼성전자dsr연구동','','partner_csv','manual:lost112:V0007698'),
('partner_api','신라면세점 서울점','신라면세점서울점','','partner_csv','manual:lost112:V0007737'),
('partner_api','올리브영(영종하늘도시점)','올리브영영종하늘도시점','','partner_csv','manual:lost112:V0007727'),
('partner_api','웨스틴 서울 파르나스호텔','웨스틴서울파르나스호텔','','partner_csv','manual:lost112:V0007680'),
('partner_api','유니클로(디타워점)','유니클로디타워점','','partner_csv','manual:lost112:V0007717'),
('partner_api','인하대학교 학생지원팀','인하대학교학생지원팀','','partner_csv','manual:lost112:V0007751'),
('partner_api','진접차량사업소','진접차량사업소','','partner_csv','manual:lost112:V0003880'),
('partner_api','타임테라스','타임테라스','','partner_csv','partner:11151d985a30d007f87a'),
('partner_api','팽성 청담 스포츠센터','팽성청담스포츠센터','','partner_csv','manual:lost112:V0007714'),
('partner_api','한성대학교','한성대학교','','partner_csv','manual:lost112:V0004100'),
('partner_api','인천교통공사 송도파트','인천교통공사송도파트','','partner_csv','manual:lost112:V0007720')
) as r(item_source_code,alias_name,normalized_alias,region_code,location_source_code,source_key)
loop
select id into strict target_id from public.storage_locations where location_source_code=reviewed.location_source_code and source_key=reviewed.source_key and is_active;
if exists (select 1 from public.storage_location_aliases where item_source_code=reviewed.item_source_code and normalized_alias=reviewed.normalized_alias and region_code=reviewed.region_code and (storage_location_id<>target_id or not verified or match_method<>'manual')) then raise exception 'Conflicting alias: %',reviewed.alias_name; end if;
insert into public.storage_location_aliases (item_source_code,alias_name,normalized_alias,region_code,storage_location_id,match_method,verified)
select reviewed.item_source_code,reviewed.alias_name,reviewed.normalized_alias,reviewed.region_code,target_id,'manual',true
where not exists(select 1 from public.storage_location_aliases where item_source_code=reviewed.item_source_code and normalized_alias=reviewed.normalized_alias and region_code=reviewed.region_code)
on conflict (item_source_code,normalized_alias,region_code) do nothing;
end loop;
end;
$aliases$;
commit;
