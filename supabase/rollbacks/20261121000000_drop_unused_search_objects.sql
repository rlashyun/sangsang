-- 수동 복구 자료 — 자동 실행하거나 migrations/로 옮기지 않는다(supabase/README.md).
-- 20261121000000_drop_unused_search_objects.sql을 되돌린다. 정의는 2026-11-21 삭제 직전 운영 정의(pg_get_functiondef)와 같다.
-- 색인 재생성은 쓰기를 잠그므로 운영 중이면 create index concurrently로 따로 실행한다(트랜잭션 밖).

create index if not exists found_item_search_documents_kind
    on public.found_item_search_documents using btree (storage_location_id, kind_mask, item_id) include (content_hash);
create index if not exists search_engine_matched_location_id_idx
    on public.found_items using btree (storage_location_id, id) where (location_match_status = 'matched'::text);

create or replace function public.allowed_search_kind_masks(p_kinds text[])
returns integer[] language sql immutable parallel safe set search_path='' as $function$
 with wanted as (select
  (case when p_kinds && array['지갑','장지갑','반지갑','머니클립'] then 1 else 0 end) |
  (case when p_kinds && array['휴대폰','핸드폰','스마트폰','아이폰'] then 2 else 0 end) |
  (case when p_kinds && array['이어폰','에어팟','버즈'] then 4 else 0 end) |
  (case when p_kinds && array['모자','썬캡','선캡','캡모자'] then 8 else 0 end) |
  (case when p_kinds && array['가방','백팩','배낭'] then 16 else 0 end) |
  (case when p_kinds && array['카드','신용','체크카드','교통카드'] then 32 else 0 end) as mask
 ) select array_agg(v) from generate_series(0,63) v,wanted w
 where coalesce(cardinality(p_kinds),0)=0 or (v&w.mask)<>0
$function$;

create or replace function public.search_found_items_semantic(p_location_ids bigint[], p_embedding extensions.vector)
returns table(item_id bigint, similarity double precision)
language sql stable set search_path='' set statement_timeout='4s' set jit='off' as $function$
  select f.id, 1 - (e.embedding::extensions.vector(1024) operator(extensions.<=>) p_embedding) as similarity
  from public.found_items f
  join public.found_item_embeddings_bge_m3 e on e.item_id = f.id
  where cardinality(p_location_ids) between 1 and 300
    and f.storage_location_id = any(p_location_ids)
    and f.location_match_status = 'matched'
    and e.is_current and e.model = 'baai/bge-m3' and e.provider = 'deepinfra'
    and e.input_version = 'color_ko_item_name:v2'
    and 1 - (e.embedding::extensions.vector(1024) operator(extensions.<=>) p_embedding) >= 0.55
  order by e.embedding::extensions.vector(1024) operator(extensions.<=>) p_embedding, f.id desc
  limit 200
$function$;

create or replace function public.read_found_item_vector_scores(p_ids bigint[], p_locations bigint[], p_vector extensions.vector)
returns table(item_id bigint, storage_location_id bigint, content_hash text, distance double precision)
language sql stable set search_path='' set jit='off' set enable_indexscan='off'
set effective_io_concurrency='64' set work_mem='16MB' as $function$
 select e.item_id,e.storage_location_id,e.content_hash,
 e.half_embedding::extensions.vector(1024) operator(extensions.<=>) p_vector
 from public.found_item_search_vectors e
 where e.item_id=any(p_ids) and e.storage_location_id=any(p_locations)
$function$;

create or replace function public.rerank_found_item_candidates(p_location_ids bigint[], p_item_ids bigint[], p_embedding extensions.vector default null)
returns table(item_id bigint, similarity double precision, detail jsonb)
language sql stable set search_path='' set jit='off' set statement_timeout='1500ms' as $function$
 select d.item_id,
  case when p_embedding is not null and e.item_id is not null then
   1-(e.half_embedding::extensions.vector(1024) operator(extensions.<=>) p_embedding)
  end as similarity,d.detail
 from public.found_item_search_documents d
 left join public.found_item_search_vectors e on p_embedding is not null
  and e.item_id=d.item_id and e.storage_location_id=d.storage_location_id
 where d.item_id=any(p_item_ids) and d.storage_location_id=any(p_location_ids)
  and cardinality(p_item_ids) between 1 and 200 and cardinality(p_location_ids) between 1 and 300
$function$;

create or replace function public.search_found_item_candidates(p_location_ids bigint[], p_terms text[], p_kinds text[] default null)
returns jsonb language sql stable set search_path='' set statement_timeout='4s' set jit='off' as $function$
 with matched as materialized (
  select d.item_id from public.found_item_search_documents d
  where d.storage_location_id=any(p_location_ids) and cardinality(p_location_ids) between 1 and 300
   and d.kind_mask=any(public.allowed_search_kind_masks(p_kinds))
   and exists(select 1 from unnest(p_terms) t(term) where term<>'' and strpos(d.search_text,lower(t.term))>0)
 ) select jsonb_build_object('candidate_count',(
  select count(*) from public.found_item_search_documents d
  where d.storage_location_id=any(p_location_ids) and cardinality(p_location_ids) between 1 and 300),
  'items',coalesce((select jsonb_agg(d.detail) from public.found_item_search_documents d
   where d.item_id=any(array(select m.item_id from matched m))),'[]'::jsonb))
$function$;

create or replace function public.search_found_items_semantic_fast(p_location_ids bigint[], p_embedding extensions.vector, p_kinds text[] default null)
returns table(item_id bigint, similarity double precision)
language sql stable set search_path='' set statement_timeout='4s' set jit='off' as $function$
 with candidates as materialized (
  select d.item_id,d.storage_location_id,d.content_hash from public.found_item_search_documents d
  where d.storage_location_id=any(p_location_ids) and cardinality(p_location_ids) between 1 and 300
   and d.kind_mask=any(public.allowed_search_kind_masks(p_kinds))
 ) select v.item_id,1-v.distance
 from public.read_found_item_vector_scores(array(select c.item_id from candidates c),p_location_ids,p_embedding) v
 join candidates c on c.item_id=v.item_id
  and c.storage_location_id=v.storage_location_id
 where v.distance<=0.45 order by v.distance,v.item_id desc limit 200
$function$;

do $$
declare signature text;
begin
 foreach signature in array array[
  'public.allowed_search_kind_masks(text[])',
  'public.search_found_items_semantic(bigint[],extensions.vector)',
  'public.read_found_item_vector_scores(bigint[],bigint[],extensions.vector)',
  'public.rerank_found_item_candidates(bigint[],bigint[],extensions.vector)',
  'public.search_found_item_candidates(bigint[],text[],text[])',
  'public.search_found_items_semantic_fast(bigint[],extensions.vector,text[])'] loop
  execute format('revoke all on function %s from public,anon,authenticated',signature);
  execute format('grant execute on function %s to service_role',signature);
 end loop;
end $$;
