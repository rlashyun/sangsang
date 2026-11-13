-- PRD 4.5 — materialize tiny kind bits and cover candidate IDs/hashes in the index.
-- Only the derived document table is rewritten. Original rows/vectors are unchanged.
set lock_timeout='2s';
drop index public.found_item_search_documents_kind;
alter table public.found_item_search_documents add column kind_mask integer
 generated always as (public.search_kind_mask(search_text)) stored;
create index found_item_search_documents_kind
 on public.found_item_search_documents(storage_location_id,kind_mask,item_id) include(content_hash);
analyze public.found_item_search_documents;
reset lock_timeout;

create function public.read_found_item_vector_scores(
 p_ids bigint[],p_locations bigint[],p_vector extensions.vector(1024))
returns table(item_id bigint,storage_location_id bigint,content_hash text,distance double precision)
language sql stable security invoker set search_path='' set jit='off' set enable_indexscan='off'
set effective_io_concurrency='64' set work_mem='16MB' as $$
 select e.item_id,e.storage_location_id,e.content_hash,
 coalesce(e.full_embedding,e.half_embedding::extensions.vector(1024)) operator(extensions.<=>) p_vector
 from public.found_item_search_vectors e
 where e.item_id=any(p_ids) and e.storage_location_id=any(p_locations)
$$;
revoke all on function public.read_found_item_vector_scores(bigint[],bigint[],extensions.vector) from public,anon,authenticated;
grant execute on function public.read_found_item_vector_scores(bigint[],bigint[],extensions.vector) to service_role;

create or replace function public.search_found_items_semantic_fast(
 p_location_ids bigint[],p_embedding extensions.vector(1024),p_kinds text[] default null)
returns table(item_id bigint,similarity double precision)
language sql stable security invoker set search_path='' set statement_timeout='4s' set jit='off' as $$
 with candidates as materialized (
  select d.item_id,d.storage_location_id,d.content_hash from public.found_item_search_documents d
  where d.storage_location_id=any(p_location_ids) and cardinality(p_location_ids) between 1 and 300
   and d.kind_mask=any(public.allowed_search_kind_masks(p_kinds))
 ) select v.item_id,1-v.distance
 from public.read_found_item_vector_scores(array(select c.item_id from candidates c),p_location_ids,p_embedding) v
 join candidates c on c.item_id=v.item_id and c.content_hash=v.content_hash
  and c.storage_location_id=v.storage_location_id
 where v.distance<=0.45 order by v.distance,v.item_id desc limit 200
$$;

create or replace function public.search_found_item_candidates(p_location_ids bigint[],p_terms text[],p_kinds text[] default null)
returns jsonb language sql stable security invoker set search_path='' set statement_timeout='4s' set jit='off' as $$
 with matched as materialized (
  select d.item_id from public.found_item_search_documents d
  where d.storage_location_id=any(p_location_ids) and cardinality(p_location_ids) between 1 and 300
   and d.kind_mask=any(public.allowed_search_kind_masks(p_kinds))
   and exists(select 1 from unnest(p_terms) t(term) where term<>'' and strpos(d.search_text,lower(t.term))>0)
 ) select jsonb_build_object('candidate_count',(
  select count(*) from public.found_item_search_documents d
  where d.storage_location_id=any(p_location_ids) and cardinality(p_location_ids) between 1 and 300),
  'items',coalesce((select jsonb_agg(d.detail) from public.found_item_search_documents d
   where d.item_id=any(array(select m.item_id from matched m))),'[]'::jsonb))
$$;
