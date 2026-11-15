-- PRD 4.5 — no full-scope count or full detail assembly on keyword retrieval.
drop function public.keyword_found_item_candidates(bigint[],text,text[],text[]);
create function public.keyword_found_item_candidates(p_location_ids bigint[],p_terms text[],p_kinds text[] default null)
returns jsonb language sql stable security invoker set search_path='' set jit='off' set statement_timeout='1800ms' as $$
 with pool as materialized (
  select d.item_id from public.found_item_search_documents d
  where d.storage_location_id=any(p_location_ids) and cardinality(p_location_ids) between 1 and 300
   and d.kind_mask=any(public.allowed_search_kind_masks(p_kinds))
   and (coalesce(cardinality(p_kinds),0)>0 or exists(
    select 1 from unnest(p_terms) t(term) where term<>'' and strpos(d.search_text,lower(t.term))>0))
 ) select jsonb_build_object('candidate_count',null,
  'items',coalesce((select jsonb_agg(jsonb_build_object('id',d.item_id,
   'storage_location_id',d.storage_location_id,'item_name',d.detail->>'item_name',
   'raw_category_name',d.detail->>'raw_category_name','color_name',d.detail->>'color_name',
   'description',d.detail->>'description'))
   from public.found_item_search_documents d where d.item_id=any(array(select item_id from pool))),'[]'::jsonb))
$$;
revoke all on function public.keyword_found_item_candidates(bigint[],text[],text[]) from public,anon,authenticated;
grant execute on function public.keyword_found_item_candidates(bigint[],text[],text[]) to service_role;
