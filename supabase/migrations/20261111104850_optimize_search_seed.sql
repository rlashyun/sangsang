-- PRD 4.5 — exclude raw_payload from materialized bootstrap rows. No API/schema contract change.
create or replace function public.seed_found_item_search(p_after bigint,p_limit integer default 2000)
returns jsonb language sql security invoker set search_path='' set statement_timeout='45s' as $$
 with source as materialized (
  select f.id,f.storage_location_id,f.location_match_status,f.normalized_search_text,f.item_name,f.raw_category_name,f.color_name,f.description,f.image_url,f.registered_on,f.raw_storage_name,f.item_source_code,f.atc_id,f.found_sequence,encode(extensions.digest(public.bge_m3_input(f.normalized_search_text,f.item_name),'sha256'),'hex') h
  from public.found_items f where f.id>p_after order by f.id limit least(greatest(p_limit,1),5000) for share of f
 ), docs as (
  insert into public.found_item_search_documents
  select s.id,s.storage_location_id,s.h,lower(concat_ws(' ',s.item_name,s.raw_category_name,s.color_name,s.description)),
   jsonb_build_object('id',s.id,'storage_location_id',s.storage_location_id,
   'item_name',s.item_name,'raw_category_name',s.raw_category_name,'color_name',s.color_name,
   'description',s.description,'image_url',s.image_url,'registered_on',s.registered_on,
   'raw_storage_name',s.raw_storage_name,'item_source_code',s.item_source_code,
   'atc_id',s.atc_id,'found_sequence',s.found_sequence) from source s
  where s.location_match_status='matched' and s.storage_location_id is not null on conflict do nothing returning item_id
 ), vecs as (
  insert into public.found_item_search_vectors
  select e.item_id,s.storage_location_id,s.h,
   case when e.embedding operator(extensions.=) e.embedding::extensions.halfvec(1024)::extensions.vector(1024)
    then e.embedding::extensions.halfvec(1024) end,
   case when e.embedding operator(extensions.<>) e.embedding::extensions.halfvec(1024)::extensions.vector(1024)
    then e.embedding end
  from source s join public.found_item_embeddings_bge_m3 e on e.item_id=s.id
  where s.location_match_status='matched' and e.is_current and e.content_hash=s.h
   and e.model='baai/bge-m3' and e.provider='deepinfra' and e.input_version='normalized_search_text_or_item_name:v1'
  on conflict do nothing returning item_id
 ) select jsonb_build_object('last_id',coalesce(max(id),p_after),'read',count(*),
  'documents',(select count(*) from docs),'vectors',(select count(*) from vecs)) from source
$$;

