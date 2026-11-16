-- PRD 4.1: space first, vector ranking second; experimental service-role RPC only.
-- Keep full float32 vectors; train a compact cosine index after the full corpus is loaded.
-- IVFFlat avoids the growing in-memory graph needed by HNSW on this compute tier.
set statement_timeout='0';
set maintenance_work_mem='128MB';
create index found_item_embeddings_bge_m3_cosine_ivfflat
 on public.found_item_embeddings_bge_m3 using ivfflat
 ((embedding::extensions.halfvec(1024)) extensions.halfvec_cosine_ops)
 with(lists=256) where is_current;

create function public.match_bge_m3_scoped(query_embedding extensions.vector(1024),
 location_ids bigint[],match_count integer default 50,exact_search boolean default false)
returns table(item_id bigint,storage_location_id bigint,score double precision)
language plpgsql set search_path='' set statement_timeout='55s' set work_mem='16MB'
 set ivfflat.iterative_scan='relaxed_order'
 set ivfflat.probes=32 set ivfflat.max_probes=256 as $$
begin
 if query_embedding is null or extensions.vector_dims(query_embedding)<>1024
    or extensions.vector_norm(query_embedding)=0 or match_count is null or match_count<1 or match_count>100
    or cardinality(location_ids) is null or cardinality(location_ids)=0 then
  raise exception 'invalid scoped search arguments' using errcode='22023';
 end if;
 if exact_search then
  return query with candidates as materialized (
   select e.item_id,f.storage_location_id,
          e.embedding operator(extensions.<=>) query_embedding as distance
   from public.found_item_embeddings_bge_m3 e
   join public.found_items f on f.id=e.item_id
   join public.storage_locations l on l.id=f.storage_location_id and l.is_active
   join public.location_sources ls on ls.code=l.location_source_code and ls.enabled
   where e.is_current and f.location_match_status='matched' and f.storage_location_id=any(location_ids)
  ) select c.item_id,c.storage_location_id,
     1-c.distance/2
    from candidates c order by c.distance,c.item_id limit match_count;
 else
  return query with candidates as materialized (
   select e.item_id,f.storage_location_id,e.embedding
   from public.found_item_embeddings_bge_m3 e
   join public.found_items f on f.id=e.item_id
   join public.storage_locations l on l.id=f.storage_location_id and l.is_active
   join public.location_sources ls on ls.code=l.location_source_code and ls.enabled
   where e.is_current and f.location_match_status='matched' and f.storage_location_id=any(location_ids)
   order by e.embedding::extensions.halfvec(1024) operator(extensions.<=>)
            query_embedding::extensions.halfvec(1024)
   limit greatest(200,match_count*4)
  ) select c.item_id,c.storage_location_id,
     1-(c.embedding operator(extensions.<=>) query_embedding)/2
    from candidates c order by c.embedding operator(extensions.<=>) query_embedding,c.item_id limit match_count;
 end if;
end $$;
revoke all on function public.match_bge_m3_scoped(extensions.vector,bigint[],integer,boolean) from public,anon,authenticated;
grant execute on function public.match_bge_m3_scoped(extensions.vector,bigint[],integer,boolean) to service_role;
analyze public.found_item_embeddings_bge_m3;
reset statement_timeout;
reset maintenance_work_mem;
