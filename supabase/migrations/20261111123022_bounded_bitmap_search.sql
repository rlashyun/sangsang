-- PRD 4.5 — bitmap-prefetch selected vectors instead of thousands of scalar PK reads.
create or replace function public.search_found_items_semantic_fast(
 p_location_ids bigint[],p_embedding extensions.vector(1024),p_kinds text[] default null)
returns table(item_id bigint,similarity double precision)
language sql stable security invoker set search_path='' set statement_timeout='4s' set jit='off'
set enable_indexscan='off' set effective_io_concurrency='64' as $$
 with candidates as materialized (
  select d.item_id,d.storage_location_id,d.content_hash
  from public.found_item_search_documents d
  where d.storage_location_id=any(p_location_ids) and cardinality(p_location_ids) between 1 and 300
   and public.search_kind_matches(d.search_text,p_kinds)
 ), distances as materialized (
  select e.item_id,e.storage_location_id,e.content_hash,
   coalesce(e.full_embedding,e.half_embedding::extensions.vector(1024))
    operator(extensions.<=>) p_embedding as distance
  from public.found_item_search_vectors e
  where e.item_id=any(array(select c.item_id from candidates c))
   and e.storage_location_id=any(p_location_ids)
 ) select v.item_id,1-v.distance from distances v join candidates c
 on c.item_id=v.item_id and c.content_hash=v.content_hash and c.storage_location_id=v.storage_location_id
 where v.distance<=0.45 order by v.distance,v.item_id desc limit 200
$$;

create or replace function public.search_found_item_candidates(p_location_ids bigint[],p_terms text[],p_kinds text[] default null)
returns jsonb language sql stable security invoker set search_path='' set statement_timeout='4s' set jit='off'
set enable_indexscan='off' set effective_io_concurrency='64' as $$
 with lexical as materialized (
  select d.item_id,d.search_text from public.found_item_search_documents d
  where d.storage_location_id=any(p_location_ids) and cardinality(p_location_ids) between 1 and 300
   and exists(select 1 from unnest(p_terms) t(term) where term<>'' and strpos(d.search_text,lower(t.term))>0)
 ), matched as materialized (
  select l.item_id from lexical l where public.search_kind_matches(l.search_text,p_kinds)
 ) select jsonb_build_object('candidate_count',(
  select count(*) from public.found_item_search_documents d
  where d.storage_location_id=any(p_location_ids) and cardinality(p_location_ids) between 1 and 300),
  'items',coalesce((select jsonb_agg(d.detail) from public.found_item_search_documents d
   where d.item_id=any(array(select m.item_id from matched m))),'[]'::jsonb))
$$;
