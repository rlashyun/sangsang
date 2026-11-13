-- PRD 4.5 — avoid materializing every scoped detail JSON before keyword filtering.
create or replace function public.search_found_item_candidates(p_location_ids bigint[],p_terms text[])
returns jsonb language sql stable security invoker set search_path='' set statement_timeout='1500ms' set jit='off' as $$
 with matched as materialized (
  select d.item_id from public.found_item_search_documents d
  where d.storage_location_id=any(p_location_ids) and cardinality(p_location_ids) between 1 and 300
  and exists(select 1 from unnest(p_terms) t(term)
   where length(t.term)>=1 and strpos(d.search_text,lower(t.term))>0)
 ) select jsonb_build_object('candidate_count',(
   select count(*) from public.found_item_search_documents d
   where d.storage_location_id=any(p_location_ids) and cardinality(p_location_ids) between 1 and 300),
  'items',coalesce((select jsonb_agg(r.detail) from matched m cross join lateral (
   select d.detail from public.found_item_search_documents d where d.item_id=m.item_id offset 0
  ) r),'[]'::jsonb))
$$;
