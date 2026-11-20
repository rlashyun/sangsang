-- PRD 4.5, 5 — 전체 물품 확대(약 62만 건) 뒤 새 임베딩 회차(after_id=0)의 첫 조회가 Data API 8초 제한을 넘었다.
-- 2026-11-20 실측: embedding_run_batch 8.15초, reuse_run_embeddings 10.4초. 누락 물품이 ID 끝쪽에 몰려 있어
-- 이미 임베딩된 57만 행의 힙(embedding_input)을 읽은 뒤에야 첫 누락을 찾았다.
-- 누락 ID를 먼저 부분 색인(20261120143159)만으로 고르고, 고른 행만 힙에서 읽는다(각 0.35초 이내).
-- 반환 형식·임대 확인·재사용 조건·건수 갱신은 기존과 같다. skip locked로 건너뛴 행은 다음 조회에서 다시 고른다.
set local lock_timeout = '5s';

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

revoke all on function public.embedding_run_batch(bigint,uuid,bigint,integer) from public,anon,authenticated;
grant execute on function public.embedding_run_batch(bigint,uuid,bigint,integer) to service_role;
revoke all on function public.reuse_run_embeddings(bigint,uuid,bigint,integer) from public,anon,authenticated;
grant execute on function public.reuse_run_embeddings(bigint,uuid,bigint,integer) to service_role;
