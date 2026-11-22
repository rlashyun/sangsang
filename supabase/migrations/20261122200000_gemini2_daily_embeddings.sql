-- PRD 4.5, 5 — 일일 임베딩(크론)을 Gemini Embedding 2로 전환한다(2026-11-22 사용자 확정: 물품 임베딩은 글로만).
-- 회차 수명주기(ensure/claim/checkpoint/finish)·임대·건수 규칙은 그대로 두고, '누락'을 보는 표와 저장 함수만
-- found_item_embeddings_bge_m3 → found_item_embeddings_gemini2로 바꾼다. BGE 표·함수·검색 경로는 지우지 않는다
-- (사용자 지시: BGE-M3는 당분간 보존). 앱은 같은 RPC 이름으로 Gemini 벡터를 보낸다(GeminiItemClient).
-- Gemini 표의 '현재' 기준: 행이 있으면 현재다. 물품 글(embedding_input)이 바뀌면 트리거가 그 행을 지워(1비트 행도 연쇄 삭제)
-- 다음 회차가 다시 만든다. 그래서 누락 확인은 기본키 조회 하나로 끝난다(20261120143534의 색인 경로와 같은 모양).
-- 적용 순서: 이 SQL과 앱(크론 클라이언트 교체)이 같은 PR이다. 이전 앱이 잠깐 BGE 벡터를 보내면 upsert의 차원·내용 검사가
-- 아니라 의미가 다른 벡터가 Gemini 표에 들어갈 수 있으므로, 병합 직후 첫 회차 전(09시대)까지 배포가 끝나야 한다.
set local lock_timeout = '5s';
set local statement_timeout = '120s';

-- 1) 글이 바뀐 물품의 Gemini 벡터를 지운다(앞으로).
create or replace function public.invalidate_gemini2_embedding()
returns trigger language plpgsql security invoker set search_path = '' as $$
begin
 delete from public.found_item_embeddings_gemini2 where item_id = new.id;
 return new;
end $$;
revoke all on function public.invalidate_gemini2_embedding() from public, anon, authenticated;

drop trigger if exists found_items_invalidate_gemini2 on public.found_items;
create trigger found_items_invalidate_gemini2
 after update of embedding_input on public.found_items
 for each row when (old.embedding_input is distinct from new.embedding_input)
 execute function public.invalidate_gemini2_embedding();

-- 2) 적재(2026-11-22) 이후 이미 바뀐 글의 벡터도 한 번 지운다.
delete from public.found_item_embeddings_gemini2 g
 using public.found_items f
 where f.id = g.item_id and f.embedding_input is distinct from g.embedded_input;

-- 3) 회차 생성: 누락 판단을 Gemini 표로.
create or replace function public.ensure_embedding_cycle(p_business_date date,p_run_slot text,p_manual boolean default false)
returns jsonb language plpgsql security invoker set search_path='' as $$
declare r public.embedding_runs; upper_id bigint; has_missing boolean; empty_count bigint; today_count bigint;
begin
 perform pg_advisory_xact_lock(71490311);
 if p_run_slot not in ('legacy','morning','afternoon','manual') or p_run_slot is null then
  raise exception 'invalid embedding cycle';
 end if;
 if not p_manual and not public.embedding_ingestion_ready(p_business_date,p_run_slot) then return null; end if;
 select coalesce(max(id),0) into upper_id from public.found_items;
 select count(*) into empty_count from public.found_items where embedding_input='';
 select count(*) into today_count from public.found_items
  where item_source_code in (select s.code from public.item_sources s)
  and first_seen_at>=(p_business_date::timestamp at time zone 'Asia/Seoul')
  and first_seen_at<((p_business_date+1)::timestamp at time zone 'Asia/Seoul');
 -- PRD 4.5 — 2026-11-22부터 Gemini 표 기준.
 select exists(select 1 from public.found_items f where f.embedding_input<>''
  and not exists(select 1 from public.found_item_embeddings_gemini2 e where e.item_id=f.id)) into has_missing;
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

-- 4) 같은 글로 이미 만든 Gemini 글 벡터를 재사용한다(사진 벡터는 재사용하지 않는다).
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
  and not exists(select 1 from public.found_item_embeddings_gemini2 existing where existing.item_id=f.id)
  order by f.id limit least(greatest(p_limit,1),512)
 ), pending as materialized (
  select f.id,f.embedding_input from ids join public.found_items f on f.id=ids.id
  where f.embedding_input<>''
  for share of f skip locked
 ), candidates as materialized (
  select p.id,p.embedding_input,e.embedding,e.content_hash from pending p
  join lateral (
   select s.embedding,s.content_hash from public.found_item_embeddings_gemini2 s
   where s.content_hash=encode(extensions.digest(p.embedding_input,'sha256'),'hex')
   and s.embedded_input=p.embedding_input and s.image_url is null
   limit 1
  ) e on true
 ), inserted as (
  insert into public.found_item_embeddings_gemini2(item_id,embedding,embedded_input,content_hash)
  select id,embedding,embedding_input,content_hash from candidates
  on conflict(item_id) do nothing returning item_id
 ) select count(*) into n from inserted;
 update public.embedding_runs set reused_count=reused_count+n,succeeded_count=succeeded_count+n where id=p_run_id;
 return n;
end $$;

-- 5) 배치 조회: Gemini 행이 없는 물품.
create or replace function public.embedding_run_batch(p_run_id bigint, p_token uuid, p_after bigint, p_limit integer default 64)
returns jsonb language plpgsql set search_path='' set statement_timeout='30s' as $$
declare r public.embedding_runs; rows jsonb;
begin
 select * into r from public.embedding_runs where id=p_run_id and lease_token=p_token
 and status='running' and lease_until>now();
 if not found then raise exception 'embedding lease unavailable'; end if;
 if exists(select 1 from public.ingestion_runs where status='running') then raise exception 'ingestion still running'; end if;
 if r.input_version<>'color_ko_item_name:v2' then raise exception 'unsupported embedding input version'; end if;
 perform public.reuse_run_embeddings(p_run_id,p_token,p_after);
 with ids as materialized (
  select f.id from public.found_items f
  where f.id>p_after and f.id<=r.max_item_id and f.embedding_input<>''
  and not exists(select 1 from public.found_item_embeddings_gemini2 e where e.item_id=f.id)
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

-- 6) 저장: upsert_gemini2_embeddings(글이 원문·해시와 같을 때만 저장).
create or replace function public.save_run_embeddings(p_run_id bigint,p_token uuid,records jsonb)
returns bigint language plpgsql security invoker set search_path='' set statement_timeout='30s' as $$
declare saved bigint;
begin
 perform 1 from public.embedding_runs where id=p_run_id and lease_token=p_token
 and status='running' and lease_until>now() for update;
 if not found then raise exception 'embedding lease unavailable'; end if;
 if jsonb_array_length(records)>32 then raise exception 'embedding batch too large'; end if;
 select count(*) into saved from public.upsert_gemini2_embeddings(records);
 return saved;
end $$;

-- 7) 종료: 남은 누락을 Gemini 표로 센다.
create or replace function public.finish_embedding_run(p_run_id bigint,p_token uuid,p_exhausted boolean,p_error text default null)
returns jsonb language plpgsql security invoker set search_path='' set statement_timeout='30s' as $$
declare r public.embedding_runs; remaining bigint;
begin
 select * into r from public.embedding_runs where id=p_run_id and lease_token=p_token
 and status='running' and lease_until>now() for update;
 if not found then raise exception 'embedding lease unavailable'; end if;
 select count(*) into remaining from public.found_items f where f.id<=r.max_item_id and f.embedding_input<>''
 and not exists(select 1 from public.found_item_embeddings_gemini2 e where e.item_id=f.id);
 update public.embedding_runs set remaining_count=remaining,
 status=case when remaining=0 then 'succeeded' when p_error is not null then 'failed' when p_exhausted then 'partial' else 'pending' end,
 after_id=case when p_exhausted or p_error is not null then 0 else after_id end,
 next_attempt_at=case when remaining>0 and (p_exhausted or p_error is not null) then now()+interval '15 minutes' else now() end,
 lease_token=null,lease_until=null,finished_at=case when remaining=0 then now() else null end,
 last_error=left(p_error,80)
 where id=p_run_id returning * into r;
 return to_jsonb(r)-'lease_token';
end $$;

revoke all on function public.ensure_embedding_cycle(date,text,boolean) from public,anon,authenticated;
grant execute on function public.ensure_embedding_cycle(date,text,boolean) to service_role;
revoke all on function public.reuse_run_embeddings(bigint,uuid,bigint,integer) from public,anon,authenticated;
grant execute on function public.reuse_run_embeddings(bigint,uuid,bigint,integer) to service_role;
revoke all on function public.embedding_run_batch(bigint,uuid,bigint,integer) from public,anon,authenticated;
grant execute on function public.embedding_run_batch(bigint,uuid,bigint,integer) to service_role;
revoke all on function public.save_run_embeddings(bigint,uuid,jsonb) from public,anon,authenticated;
grant execute on function public.save_run_embeddings(bigint,uuid,jsonb) to service_role;
revoke all on function public.finish_embedding_run(bigint,uuid,boolean,text) from public,anon,authenticated;
grant execute on function public.finish_embedding_run(bigint,uuid,boolean,text) to service_role;
