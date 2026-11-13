-- PRD 4.5 — bound the scanned input pool before matching reusable vectors.
create or replace function public.reuse_run_embeddings(p_run_id bigint,p_token uuid,p_after bigint,p_limit integer default 512)
returns bigint language plpgsql security invoker set search_path='' set statement_timeout='20s' as $$
declare r public.embedding_runs; n bigint;
begin
 select * into r from public.embedding_runs where id=p_run_id and lease_token=p_token
 and status='running' and lease_until>now() for update;
 if not found then raise exception 'embedding lease unavailable'; end if;
 with pending as materialized (
  select f.id,f.embedding_input from public.found_items f
  where f.id>p_after and f.id<=r.max_item_id and f.embedding_input<>''
  and not exists(select 1 from public.found_item_embeddings_bge_m3 existing where existing.item_id=f.id)
  order by f.id limit least(greatest(p_limit,1),512)
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
notify pgrst,'reload schema';
