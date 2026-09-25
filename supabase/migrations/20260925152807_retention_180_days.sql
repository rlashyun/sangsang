-- PRD 3.1-2, 4.1 — 연계기관과 경찰관서 모두 등록일 기준 180일을 보존합니다.
update public.item_sources
set retention_days = 180
where code in ('partner_api', 'police_api')
  and retention_days <> 180;
