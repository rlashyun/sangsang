-- PRD 4.5 — reuse only vectors generated after the v2 reset for identical input.
create index found_item_embeddings_v2_hash on public.found_item_embeddings_bge_m3(content_hash);
alter table public.embedding_runs add column reused_count bigint not null default 0;

create function public.reuse_run_embeddings(p_run_id bigint,p_token uuid,p_after bigint,p_limit integer default 512)
returns bigint language plpgsql security invoker set search_path='' set statement_timeout='20s' as $$
declare r public.embedding_runs; n bigint;
begin
 select * into r from public.embedding_runs where id=p_run_id and lease_token=p_token
 and status='running' and lease_until>now() for update;
 if not found then raise exception 'embedding lease unavailable'; end if;
 with candidates as materialized (
  select f.id,f.embedding_input,e.embedding,e.content_hash,e.embedded_at
  from public.found_items f
  join lateral (
   select s.embedding,s.content_hash,s.embedded_at from public.found_item_embeddings_bge_m3 s
   where s.content_hash=encode(extensions.digest(f.embedding_input,'sha256'),'hex')
   and s.embedded_input=f.embedding_input and s.input_version='color_ko_item_name:v2'
   and s.is_current and s.model='baai/bge-m3' and s.provider='deepinfra'
   limit 1
  ) e on true
  where f.id>p_after and f.id<=r.max_item_id and f.embedding_input<>''
  and not exists(select 1 from public.found_item_embeddings_bge_m3 existing where existing.item_id=f.id)
  order by f.id limit least(greatest(p_limit,1),512)
  for share of f skip locked
 ), inserted as (
  insert into public.found_item_embeddings_bge_m3(item_id,embedding,content_hash,embedded_input,vector_source,embedded_at)
  select id,embedding,content_hash,embedding_input,'openrouter_half_v2',embedded_at from candidates
  on conflict(item_id) do nothing returning item_id
 ) select count(*) into n from inserted;
 update public.embedding_runs set reused_count=reused_count+n,succeeded_count=succeeded_count+n
 where id=p_run_id;
 return n;
end $$;
revoke all on function public.reuse_run_embeddings(bigint,uuid,bigint,integer) from public,anon,authenticated;
grant execute on function public.reuse_run_embeddings(bigint,uuid,bigint,integer) to service_role;

create or replace function public.embedding_run_batch(p_run_id bigint,p_token uuid,p_after bigint,p_limit integer default 64)
returns jsonb language plpgsql security invoker set search_path='' set statement_timeout='30s' as $$
declare r public.embedding_runs; rows jsonb;
begin
 select * into r from public.embedding_runs where id=p_run_id and lease_token=p_token
 and status='running' and lease_until>now();
 if not found then raise exception 'embedding lease unavailable'; end if;
 if exists(select 1 from public.ingestion_runs where status='running') then raise exception 'ingestion still running'; end if;
 perform public.reuse_run_embeddings(p_run_id,p_token,p_after);
 select coalesce(jsonb_agg(to_jsonb(s)),'[]'::jsonb) into rows from (
  select f.id as item_id,f.embedding_input as embedded_input,
   encode(extensions.digest(f.embedding_input,'sha256'),'hex') as content_hash
  from public.found_items f
  where f.id>p_after and f.id<=r.max_item_id and f.embedding_input<>''
  and not exists(select 1 from public.found_item_embeddings_bge_m3 e where e.item_id=f.id and e.input_version=r.input_version and e.is_current)
  order by f.id limit least(greatest(p_limit,1),64)
 ) s;
 return rows;
end $$;
notify pgrst,'reload schema';
