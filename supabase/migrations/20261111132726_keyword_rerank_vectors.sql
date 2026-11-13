-- PRD 4.5 — backend-only binary vectors for at most 200 keyword candidates.
create function public.keyword_rerank_vectors(p_location_ids bigint[],p_item_ids bigint[])
returns table(item_id bigint,detail jsonb,vector text)
language sql stable security invoker set search_path='' set jit='off' set statement_timeout='1500ms' as $$
 select d.item_id,d.detail,
  case when e.item_id is not null then encode(extensions.vector_send(
    coalesce(e.full_embedding,e.half_embedding::extensions.vector(1024))),'base64') end
 from public.found_item_search_documents d left join public.found_item_search_vectors e
  on e.item_id=d.item_id and e.content_hash=d.content_hash and e.storage_location_id=d.storage_location_id
 where d.item_id=any(p_item_ids) and d.storage_location_id=any(p_location_ids)
  and cardinality(p_item_ids) between 1 and 200 and cardinality(p_location_ids) between 1 and 300
$$;
revoke all on function public.keyword_rerank_vectors(bigint[],bigint[]) from public,anon,authenticated;
grant execute on function public.keyword_rerank_vectors(bigint[],bigint[]) to service_role;
