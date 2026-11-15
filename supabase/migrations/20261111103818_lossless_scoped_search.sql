-- PRD 4.1/4.5/5 — derived, synchronously maintained exact-search projection.
-- Original items/vectors/functions remain unchanged. See matching rollback SQL.
set lock_timeout = '2s';
create table public.found_item_search_documents (
  item_id bigint primary key references public.found_items(id) on delete cascade,
  storage_location_id bigint,
  content_hash text,
  search_text text not null,
  detail jsonb not null
);
create index found_item_search_documents_location on public.found_item_search_documents(storage_location_id,item_id);
create table public.found_item_search_vectors (
  item_id bigint primary key references public.found_item_embeddings_bge_m3(item_id) on delete cascade,
  storage_location_id bigint,
  content_hash text not null,
  half_embedding extensions.halfvec(1024),
  full_embedding extensions.vector(1024),
  check ((half_embedding is null) <> (full_embedding is null))
);
alter table public.found_item_search_vectors alter column half_embedding set storage plain;
alter table public.found_item_search_vectors alter column full_embedding set storage plain;
create index found_item_search_vectors_location on public.found_item_search_vectors(storage_location_id,item_id);
alter table public.found_item_search_documents enable row level security;
alter table public.found_item_search_vectors enable row level security;
revoke all on public.found_item_search_documents,public.found_item_search_vectors from public,anon,authenticated;
grant select,insert,update,delete on public.found_item_search_documents,public.found_item_search_vectors to service_role;

create function public.search_document(f public.found_items) returns jsonb
language sql immutable security invoker set search_path='' as $$
 select jsonb_build_object('id',f.id,'storage_location_id',f.storage_location_id,
 'item_name',f.item_name,'raw_category_name',f.raw_category_name,'color_name',f.color_name,
 'description',f.description,'image_url',f.image_url,'registered_on',f.registered_on,
 'raw_storage_name',f.raw_storage_name,'item_source_code',f.item_source_code,
 'atc_id',f.atc_id,'found_sequence',f.found_sequence)
$$;

create function public.sync_search_document() returns trigger
language plpgsql security invoker set search_path='' as $$
declare h text;
begin
 h := encode(extensions.digest(public.bge_m3_input(new.normalized_search_text,new.item_name),'sha256'),'hex');
 if new.location_match_status='matched' and new.storage_location_id is not null then
  insert into public.found_item_search_documents values(new.id,new.storage_location_id,h,
   lower(concat_ws(' ',new.item_name,new.raw_category_name,new.color_name,new.description)),public.search_document(new))
  on conflict(item_id) do update set storage_location_id=excluded.storage_location_id,
   content_hash=excluded.content_hash,search_text=excluded.search_text,detail=excluded.detail;
  delete from public.found_item_search_vectors where item_id=new.id and content_hash is distinct from h;
  perform public.refresh_search_vector(new.id);
 else
  delete from public.found_item_search_documents where item_id=new.id;
  delete from public.found_item_search_vectors where item_id=new.id;
 end if;
 return new;
end $$;
create trigger found_items_sync_search after insert or update on public.found_items
 for each row execute function public.sync_search_document();

create function public.refresh_search_vector(p_item_id bigint) returns void
language plpgsql security invoker set search_path='' as $$
declare f public.found_items; e public.found_item_embeddings_bge_m3; h text; lossless boolean;
begin
 select * into e from public.found_item_embeddings_bge_m3 where item_id=p_item_id;
 select * into f from public.found_items where id=p_item_id;
 h := encode(extensions.digest(public.bge_m3_input(f.normalized_search_text,f.item_name),'sha256'),'hex');
 if e.is_current and e.content_hash=h and e.model='baai/bge-m3' and e.provider='deepinfra'
 and e.input_version='normalized_search_text_or_item_name:v1' and f.location_match_status='matched' then
  lossless := e.embedding operator(extensions.=) e.embedding::extensions.halfvec(1024)::extensions.vector(1024);
  insert into public.found_item_search_vectors values(e.item_id,f.storage_location_id,h,
   case when lossless then e.embedding::extensions.halfvec(1024) end,
   case when not lossless then e.embedding end)
  on conflict(item_id) do update set storage_location_id=excluded.storage_location_id,
   content_hash=excluded.content_hash,half_embedding=excluded.half_embedding,full_embedding=excluded.full_embedding;
 else
  delete from public.found_item_search_vectors where item_id=e.item_id;
 end if;
 return;
end $$;
create function public.sync_search_vector() returns trigger
language plpgsql security invoker set search_path='' as $$
begin
 perform public.refresh_search_vector(new.item_id);
 return new;
end $$;
create trigger embeddings_sync_search after insert or update on public.found_item_embeddings_bge_m3
 for each row execute function public.sync_search_vector();

-- Resumable, conflict-safe bootstrap; existing trigger writes win over bootstrap snapshots.
create function public.seed_found_item_search(p_after bigint,p_limit integer default 2000)
returns jsonb language sql security invoker set search_path='' set statement_timeout='45s' as $$
 with source as materialized (
  select f.*,encode(extensions.digest(public.bge_m3_input(f.normalized_search_text,f.item_name),'sha256'),'hex') h
  from public.found_items f where f.id>p_after order by f.id limit least(greatest(p_limit,1),5000) for share of f
 ), docs as (
  insert into public.found_item_search_documents
  select s.id,s.storage_location_id,s.h,lower(concat_ws(' ',s.item_name,s.raw_category_name,s.color_name,s.description)),
   public.search_document(f) from source s join public.found_items f on f.id=s.id
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

create function public.search_found_items_semantic_fast(p_location_ids bigint[],p_embedding extensions.vector(1024))
returns table(item_id bigint,similarity double precision)
language sql stable security invoker set search_path='' set statement_timeout='2500ms' set jit='off' as $$
 select v.item_id,1-v.distance from unnest(p_location_ids) l(id)
 cross join lateral (
  select e.item_id,(coalesce(e.full_embedding,e.half_embedding::extensions.vector(1024))
   operator(extensions.<=>) p_embedding) distance
  from public.found_item_search_vectors e
  join public.found_item_search_documents d on d.item_id=e.item_id and d.content_hash=e.content_hash
  where e.storage_location_id=l.id and d.storage_location_id=l.id
   and cardinality(p_location_ids) between 1 and 300
  offset 0
 ) v where v.distance<=0.45 order by v.distance,v.item_id desc limit 200
$$;

create function public.search_found_item_candidates(p_location_ids bigint[],p_terms text[])
returns jsonb language sql stable security invoker set search_path='' set statement_timeout='1500ms' set jit='off' as $$
 with scoped as materialized (
  select d.* from public.found_item_search_documents d where d.storage_location_id=any(p_location_ids)
   and cardinality(p_location_ids) between 1 and 300
 ), matched as (
  select detail from scoped d where exists(select 1 from unnest(p_terms) t(term)
   where length(t.term)>=1 and strpos(d.search_text,lower(t.term))>0)
 ) select jsonb_build_object('candidate_count',(select count(*) from scoped),
  'items',coalesce((select jsonb_agg(detail) from matched),'[]'::jsonb))
$$;

create function public.search_found_item_details(p_location_ids bigint[],p_item_ids bigint[])
returns setof jsonb language sql stable security invoker set search_path='' set statement_timeout='1000ms' as $$
 select d.detail from public.found_item_search_documents d where d.item_id=any(p_item_ids)
 and d.storage_location_id=any(p_location_ids) and cardinality(p_location_ids) between 1 and 300
 and cardinality(p_item_ids)<=200
$$;

revoke all on function public.search_document(public.found_items),public.refresh_search_vector(bigint),public.sync_search_document(),public.sync_search_vector(),
 public.seed_found_item_search(bigint,integer),public.search_found_items_semantic_fast(bigint[],extensions.vector),
 public.search_found_item_candidates(bigint[],text[]),public.search_found_item_details(bigint[],bigint[]) from public,anon,authenticated;
grant execute on function public.search_document(public.found_items),public.refresh_search_vector(bigint),public.sync_search_document(),public.sync_search_vector(),
 public.seed_found_item_search(bigint,integer),public.search_found_items_semantic_fast(bigint[],extensions.vector),
 public.search_found_item_candidates(bigint[],text[]),public.search_found_item_details(bigint[],bigint[]) to service_role;
reset lock_timeout;
