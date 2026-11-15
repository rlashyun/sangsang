-- PRD 4.5 — keyword retrieval first; vectors only rerank a bounded shortlist.
-- Old experimental RPCs stay available for rollback but are no longer called by the app.
create function public.keyword_found_item_candidates(
 p_location_ids bigint[],p_query text,p_terms text[],p_kinds text[] default null)
returns jsonb language sql stable security invoker set search_path='' set jit='off'
set statement_timeout='1800ms' as $$
 with hits as materialized (
  select d.item_id,d.storage_location_id,d.search_text,
   (select count(*) from unnest(string_to_array(lower(p_query),' ')) w(term)
    where term<>'' and strpos(d.search_text,w.term)>0) as word_hits
  from public.found_item_search_documents d
  where d.storage_location_id=any(p_location_ids) and cardinality(p_location_ids) between 1 and 300
   and d.kind_mask=any(public.allowed_search_kind_masks(p_kinds))
   and exists(select 1 from unnest(p_terms) t(term) where term<>'' and strpos(d.search_text,lower(t.term))>0)
 ), pool as (
  select * from hits order by word_hits desc,item_id desc limit 1000
 ) select jsonb_build_object(
  'candidate_count',(select count(*) from public.found_item_search_documents d
    where d.storage_location_id=any(p_location_ids) and cardinality(p_location_ids) between 1 and 300),
  'items',coalesce((select jsonb_agg(jsonb_build_object('id',item_id,
    'storage_location_id',storage_location_id,'search_text',search_text)) from pool),'[]'::jsonb))
$$;

create function public.rerank_found_item_candidates(
 p_location_ids bigint[],p_item_ids bigint[],p_embedding extensions.vector(1024) default null)
returns table(item_id bigint,similarity double precision,detail jsonb)
language sql stable security invoker set search_path='' set jit='off' set statement_timeout='1500ms' as $$
 select d.item_id,
  case when p_embedding is not null and e.item_id is not null then
   1-(coalesce(e.full_embedding,e.half_embedding::extensions.vector(1024)) operator(extensions.<=>) p_embedding)
  end as similarity,d.detail
 from public.found_item_search_documents d
 left join public.found_item_search_vectors e on p_embedding is not null
  and e.item_id=d.item_id and e.content_hash=d.content_hash and e.storage_location_id=d.storage_location_id
 where d.item_id=any(p_item_ids) and d.storage_location_id=any(p_location_ids)
  and cardinality(p_item_ids) between 1 and 200 and cardinality(p_location_ids) between 1 and 300
$$;
revoke all on function public.keyword_found_item_candidates(bigint[],text,text[],text[]),
 public.rerank_found_item_candidates(bigint[],bigint[],extensions.vector) from public,anon,authenticated;
grant execute on function public.keyword_found_item_candidates(bigint[],text,text[],text[]),
 public.rerank_found_item_candidates(bigint[],bigint[],extensions.vector) to service_role;
