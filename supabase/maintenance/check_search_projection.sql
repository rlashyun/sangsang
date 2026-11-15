-- Read-only audit. Emits counts, not item text or vector values.
select jsonb_build_object(
 'documents',(select count(*) from public.found_item_search_documents),
 'vectors',(select count(*) from public.found_item_search_vectors),
 'missing_documents',(select count(*) from public.found_items f
  left join public.found_item_search_documents d on d.item_id=f.id
  where f.location_match_status='matched' and f.storage_location_id is not null and d.item_id is null),
 'scope_mismatch',(select count(*) from public.found_item_search_documents d
  join public.found_items f on f.id=d.item_id
  where d.storage_location_id is distinct from f.storage_location_id or f.location_match_status<>'matched'),
 'stale_vectors',(select count(*) from public.found_item_search_vectors v
  join public.found_item_embeddings_bge_m3 e on e.item_id=v.item_id
  where not e.is_current or v.content_hash<>e.content_hash),
 'document_vector_hash_mismatch',(select count(*) from public.found_item_search_vectors v
  join public.found_item_search_documents d on d.item_id=v.item_id
  where d.content_hash<>v.content_hash or d.storage_location_id is distinct from v.storage_location_id)
);

with ids as (
 (select item_id from public.found_item_search_documents order by item_id limit 100)
 union
 (select item_id from public.found_item_search_documents order by item_id desc limit 100)
) select count(*) sampled,count(*) filter(where not (
 coalesce(v.full_embedding,v.half_embedding::extensions.vector(1024)) operator(extensions.=) e.embedding
)) vector_mismatch from ids i join public.found_item_search_vectors v using(item_id)
join public.found_item_embeddings_bge_m3 e using(item_id);
