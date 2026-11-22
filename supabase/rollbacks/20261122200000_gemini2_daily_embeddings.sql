-- 되돌리기: gemini2_daily_embeddings. 수동 실행한다. 일일 임베딩 회차 함수를 BGE 표 기준(이전 정의)으로 되돌리고 무효화 트리거를 지운다.
-- 지운 Gemini 행(글이 바뀐 물품)은 되살리지 않는다. 앱도 BgeM3Client 크론으로 함께 되돌려야 한다.
set lock_timeout = '5s';
drop trigger if exists found_items_invalidate_gemini2 on public.found_items;
drop function if exists public.invalidate_gemini2_embedding();
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
create or replace function public.embedding_run_batch(p_run_id bigint, p_token uuid, p_after bigint, p_limit integer default 64)
returns jsonb language plpgsql set search_path='' set statement_timeout='30s' as $$
declare r public.embedding_runs; rows jsonb;
begin
 select * into r from public.embedding_runs where id=p_run_id and lease_token=p_token
 and status='running' and lease_until>now();
 if not found then raise exception 'embedding lease unavailable'; end if;
 if exists(select 1 from public.ingestion_runs where status='running') then raise exception 'ingestion still running'; end if;
 -- The partial index found_item_embeddings_v2_current_item_idx is written for this literal version.
 if r.input_version<>'color_ko_item_name:v2' then raise exception 'unsupported embedding input version'; end if;
 perform public.reuse_run_embeddings(p_run_id,p_token,p_after);
 with ids as materialized (
  select f.id from public.found_items f
  where f.id>p_after and f.id<=r.max_item_id and f.embedding_input<>''
  and not exists(select 1 from public.found_item_embeddings_bge_m3 e where e.item_id=f.id
   and e.input_version='color_ko_item_name:v2' and e.is_current)
  order by f.id limit least(greatest(p_limit,1),64)
 )
 select coalesce(jsonb_agg(to_jsonb(s) order by s.item_id),'[]'::jsonb) into rows from (
  select f.id as item_id,f.embedding_input as embedded_input,
   encode(extensions.digest(f.embedding_input,'sha256'),'hex') as content_hash
  from ids join public.found_items f on f.id=ids.id
  where f.embedding_input<>''
 ) s;
 return rows;
end $$;
create or replace function public.reuse_run_embeddings(p_run_id bigint, p_token uuid, p_after bigint, p_limit integer default 512)
returns bigint language plpgsql set search_path='' set statement_timeout='20s' as $$
declare r public.embedding_runs; n bigint;
begin
 select * into r from public.embedding_runs where id=p_run_id and lease_token=p_token
 and status='running' and lease_until>now() for update;
 if not found then raise exception 'embedding lease unavailable'; end if;
 with ids as materialized (
  select f.id from public.found_items f
  where f.id>p_after and f.id<=r.max_item_id and f.embedding_input<>''
  and not exists(select 1 from public.found_item_embeddings_bge_m3 existing where existing.item_id=f.id)
  order by f.id limit least(greatest(p_limit,1),512)
 ), pending as materialized (
  select f.id,f.embedding_input from ids join public.found_items f on f.id=ids.id
  where f.embedding_input<>''
  for share of f skip locked
 ), candidates as materialized (
  select p.id,p.embedding_input,e.embedding,e.content_hash,e.embedded_at from pending p
  join lateral (
   select s.embedding,s.content_hash,s.embedded_at from public.found_item_embeddings_bge_m3 s
   where s.content_hash=encode(extensions.digest(p.embedding_input,'sha256'),'hex')
   and s.embedded_input=p.embedding_input and s.input_version='color_ko_item_name:v2'
   and s.is_current and s.model='baai/bge-m3' and s.provider='deepinfra'
   limit 1
  ) e on true
 ), inserted as (
  insert into public.found_item_embeddings_bge_m3(item_id,embedding,content_hash,embedded_input,vector_source,embedded_at)
  select id,embedding,content_hash,embedding_input,'openrouter_half_v2',embedded_at from candidates
  on conflict(item_id) do nothing returning item_id
 ) select count(*) into n from inserted;
 update public.embedding_runs set reused_count=reused_count+n,succeeded_count=succeeded_count+n where id=p_run_id;
 return n;
end $$;
create or replace function public.save_run_embeddings(p_run_id bigint,p_token uuid,records jsonb)
returns bigint language plpgsql security invoker set search_path='' set statement_timeout='30s' as $$
declare saved bigint;
begin
 perform 1 from public.embedding_runs where id=p_run_id and lease_token=p_token
 and status='running' and lease_until>now() for update;
 if not found then raise exception 'embedding lease unavailable'; end if;
 if jsonb_array_length(records)>32 then raise exception 'embedding batch too large'; end if;
 select count(*) into saved from public.upsert_bge_m3_embeddings(records);
 return saved;
end $$;
create or replace function public.finish_embedding_run(p_run_id bigint,p_token uuid,p_exhausted boolean,p_error text default null)
returns jsonb language plpgsql security invoker set search_path='' set statement_timeout='30s' as $$
declare r public.embedding_runs; remaining bigint;
begin
 select * into r from public.embedding_runs where id=p_run_id and lease_token=p_token
 and status='running' and lease_until>now() for update;
 if not found then raise exception 'embedding lease unavailable'; end if;
 select count(*) into remaining from public.found_items f where f.id<=r.max_item_id and f.embedding_input<>''
 and not exists(select 1 from public.found_item_embeddings_bge_m3 e where e.item_id=f.id and e.input_version=r.input_version and e.is_current);
 update public.embedding_runs set remaining_count=remaining,
 status=case when remaining=0 then 'succeeded' when p_error is not null then 'failed' when p_exhausted then 'partial' else 'pending' end,
 after_id=case when p_exhausted or p_error is not null then 0 else after_id end,
 next_attempt_at=case when remaining>0 and (p_exhausted or p_error is not null) then now()+interval '15 minutes' else now() end,
 lease_token=null,lease_until=null,finished_at=case when remaining=0 then now() else null end,
 last_error=left(p_error,80)
 where id=p_run_id returning * into r;
 return to_jsonb(r)-'lease_token';
end $$;
-- delete from supabase_migrations.schema_migrations where version = '20261122200000';
notify pgrst, 'reload schema';
reset lock_timeout;
