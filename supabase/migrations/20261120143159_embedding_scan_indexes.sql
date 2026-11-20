-- PRD 4.5, 5 — 전체 물품 확대(2026-11-20, 약 62만 건) 뒤 임베딩 회차 생성이 Data API 8초 제한에 걸렸다.
-- ensure_embedding_cycle의 누락 확인이 found_items 전체 힙을 읽어 8.9초(57014)였고, 같은 함수가
-- 수집 완료 트리거(enqueue_embeddings_on_ingestion_finish)에서도 불려 수집 감사 기록까지 실패시켰다.
-- 두 부분 색인으로 누락 확인을 색인 전용 병합 반조인(0.45초)으로 바꾸고, 집계는 색인으로 나눠 센다.
-- 계약(인자·반환·잠금·상태 전이)은 20261116094120과 같다.
set local lock_timeout = '5s';
set local statement_timeout = '120s';

create index if not exists found_items_embedding_input_id_idx
    on public.found_items (id) where embedding_input <> '';
create index if not exists found_items_empty_embedding_input_idx
    on public.found_items (id) where embedding_input = '';
create index if not exists found_item_embeddings_v2_current_item_idx
    on public.found_item_embeddings_bge_m3 (item_id)
    where is_current and input_version = 'color_ko_item_name:v2';

create or replace function public.ensure_embedding_cycle(p_business_date date,p_run_slot text,p_manual boolean default false)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare r public.embedding_runs; upper_id bigint; has_missing boolean; empty_count bigint; today_count bigint;
begin
 perform pg_advisory_xact_lock(71490311);
 if p_run_slot not in ('legacy','morning','afternoon','manual') or p_run_slot is null then
  raise exception 'invalid embedding cycle';
 end if;
 if not p_manual and not public.embedding_ingestion_ready(p_business_date,p_run_slot) then return null; end if;
 -- Each aggregate uses its own index instead of one full heap scan.
 select coalesce(max(id),0) into upper_id from public.found_items;
 select count(*) into empty_count from public.found_items where embedding_input='';
 -- Same KST calendar day as (first_seen_at at time zone 'Asia/Seoul')::date=p_business_date,
 -- written as a range so found_items_source_first_seen_idx can serve it.
 select count(*) into today_count from public.found_items
  where item_source_code in (select s.code from public.item_sources s)
  and first_seen_at>=(p_business_date::timestamp at time zone 'Asia/Seoul')
  and first_seen_at<((p_business_date+1)::timestamp at time zone 'Asia/Seoul');
 select exists(select 1 from public.found_items f where f.embedding_input<>''
  and not exists(select 1 from public.found_item_embeddings_bge_m3 e where e.item_id=f.id
   and e.input_version='color_ko_item_name:v2' and e.is_current)) into has_missing;
 -- The shared advisory lock makes SELECT + INSERT safe and avoids consuming an
 -- identity value on every duplicate callback (INSERT ON CONFLICT does consume one).
 select * into r from public.embedding_runs where business_date=p_business_date
  and input_version='color_ko_item_name:v2' and run_slot=p_run_slot for update;
 if not found then
  insert into public.embedding_runs(business_date,run_slot,max_item_id,empty_input_count,new_today_count)
  values(p_business_date,p_run_slot,upper_id,empty_count,today_count) returning * into r;
 else
  update public.embedding_runs set
   max_item_id=greatest(max_item_id,upper_id),
   status=case when status='succeeded' and has_missing then 'pending' else status end,
   after_id=case when status='succeeded' and has_missing then 0 else after_id end,
   remaining_count=case when status='succeeded' and has_missing then null else remaining_count end,
   next_attempt_at=case when p_manual and status<>'running' then now() else next_attempt_at end,
   finished_at=case when has_missing then null else finished_at end,
   new_today_count=today_count,empty_input_count=empty_count
  where id=r.id returning * into r;
 end if;
 return to_jsonb(r)-'lease_token';
end $$;

revoke all on function public.ensure_embedding_cycle(date,text,boolean) from public,anon,authenticated;
grant execute on function public.ensure_embedding_cycle(date,text,boolean) to service_role;
