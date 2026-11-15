-- PRD 4.5 — stream compact native JSON, with a custom plan for this location array.
drop function public.keyword_found_item_candidates(bigint[],text[],text[]);
create function public.keyword_found_item_candidates(p_location_ids bigint[],p_terms text[],p_kinds text[] default null)
returns json language plpgsql stable security invoker set search_path='' set jit='off' set statement_timeout='1800ms' as $$
declare result json;
begin
 execute $query$
  select json_build_object('candidate_count',null,'items',coalesce(json_agg(r),'[]'::json))
  from (
   select d.item_id as id,d.storage_location_id,d.search_text,x.item_name,x.raw_category_name
   from public.found_item_search_documents d
   cross join lateral jsonb_to_record(d.detail) x(item_name text,raw_category_name text)
   where d.storage_location_id=any($1) and cardinality($1) between 1 and 300
    and d.kind_mask=any(public.allowed_search_kind_masks($3))
    and (coalesce(cardinality($3),0)>0 or exists(
     select 1 from unnest($2) t(term) where term<>'' and strpos(d.search_text,lower(t.term))>0))
  ) r
 $query$ into result using p_location_ids,p_terms,p_kinds;
 return result;
end $$;
revoke all on function public.keyword_found_item_candidates(bigint[],text[],text[]) from public,anon,authenticated;
grant execute on function public.keyword_found_item_candidates(bigint[],text[],text[]) to service_role;
