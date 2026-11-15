-- PRD 4.5 — push the existing final kind predicate before vector IO, not a keyword gate.
create function public.search_kind_matches(p_text text,p_kinds text[]) returns boolean
language sql immutable parallel safe security invoker set search_path='' as $$
 select coalesce(cardinality(p_kinds),0)=0 or exists(
  select 1 from unnest(p_kinds) k(term) where term<>'' and strpos((
   select regexp_replace(lower(normalize(p_text,NFKC)),'[^[:alnum:]]','','g')
  ),k.term)>0
 )
$$;
revoke all on function public.search_kind_matches(text,text[]) from public,anon,authenticated;
grant execute on function public.search_kind_matches(text,text[]) to service_role;

drop function public.search_found_items_semantic_fast(bigint[],extensions.vector);
create function public.search_found_items_semantic_fast(
 p_location_ids bigint[],p_embedding extensions.vector(1024),p_kinds text[] default null)
returns table(item_id bigint,similarity double precision)
language sql stable security invoker set search_path='' set statement_timeout='3s' set jit='off' as $$
 with candidates as materialized (
  select d.item_id,d.storage_location_id,d.content_hash
  from public.found_item_search_documents d
  where d.storage_location_id=any(p_location_ids) and cardinality(p_location_ids) between 1 and 300
   and public.search_kind_matches(d.search_text,p_kinds)
 ) select c.item_id,1-v.distance from candidates c
 cross join lateral (
  select coalesce(e.full_embedding,e.half_embedding::extensions.vector(1024))
    operator(extensions.<=>) p_embedding as distance
  from public.found_item_search_vectors e
  where e.item_id=c.item_id and e.storage_location_id=c.storage_location_id and e.content_hash=c.content_hash
  offset 0
 ) v where v.distance<=0.45 order by v.distance,c.item_id desc limit 200
$$;
revoke all on function public.search_found_items_semantic_fast(bigint[],extensions.vector,text[]) from public,anon,authenticated;
grant execute on function public.search_found_items_semantic_fast(bigint[],extensions.vector,text[]) to service_role;

drop function public.search_found_item_candidates(bigint[],text[]);
create function public.search_found_item_candidates(p_location_ids bigint[],p_terms text[],p_kinds text[] default null)
returns jsonb language sql stable security invoker set search_path='' set statement_timeout='3s' set jit='off' as $$
 with matched as materialized (
  select d.item_id from public.found_item_search_documents d
  where d.storage_location_id=any(p_location_ids) and cardinality(p_location_ids) between 1 and 300
   and public.search_kind_matches(d.search_text,p_kinds)
   and exists(select 1 from unnest(p_terms) t(term) where term<>'' and strpos(d.search_text,lower(t.term))>0)
 ) select jsonb_build_object('candidate_count',(
  select count(*) from public.found_item_search_documents d
  where d.storage_location_id=any(p_location_ids) and cardinality(p_location_ids) between 1 and 300),
  'items',coalesce((select jsonb_agg(r.detail) from matched m cross join lateral (
   select d.detail from public.found_item_search_documents d where d.item_id=m.item_id offset 0
  ) r),'[]'::jsonb))
$$;
revoke all on function public.search_found_item_candidates(bigint[],text[],text[]) from public,anon,authenticated;
grant execute on function public.search_found_item_candidates(bigint[],text[],text[]) to service_role;
