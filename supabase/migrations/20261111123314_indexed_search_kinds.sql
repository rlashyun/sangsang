-- PRD 4.5 — index the conservative kind predicate, keep final app judgement authoritative.
create function public.search_kind_mask(p_text text) returns integer
language plpgsql immutable parallel safe security invoker set search_path='' as $$
declare c text; m integer:=0;
begin
 c:=regexp_replace(lower(normalize(p_text,NFKC)),'[^[:alnum:]]','','g');
 if strpos(c,'지갑')>0 or strpos(c,'머니클립')>0 or strpos(c,'돈을넣')>0 or strpos(c,'현금을넣')>0 then m:=m|1; end if;
 if strpos(c,'휴대폰')>0 or strpos(c,'핸드폰')>0 or strpos(c,'스마트폰')>0 or strpos(c,'아이폰')>0 or strpos(c,'갤럭시')>0 then m:=m|2; end if;
 if strpos(c,'이어폰')>0 or strpos(c,'에어팟')>0 or strpos(c,'버즈')>0 or strpos(c,'귀에끼')>0 or strpos(c,'귀에넣')>0 or strpos(c,'귀에착용')>0 then m:=m|4; end if;
 if strpos(c,'모자')>0 or strpos(c,'썬캡')>0 or strpos(c,'선캡')>0 then m:=m|8; end if;
 if strpos(c,'가방')>0 or strpos(c,'백팩')>0 or strpos(c,'배낭')>0 then m:=m|16; end if;
 if strpos(c,'카드')>0 or strpos(c,'신용')>0 then m:=m|32; end if;
 return m;
end $$;

create function public.allowed_search_kind_masks(p_kinds text[]) returns integer[]
language sql immutable parallel safe security invoker set search_path='' as $$
 with wanted as (select
  (case when p_kinds && array['지갑','장지갑','반지갑','머니클립'] then 1 else 0 end) |
  (case when p_kinds && array['휴대폰','핸드폰','스마트폰','아이폰'] then 2 else 0 end) |
  (case when p_kinds && array['이어폰','에어팟','버즈'] then 4 else 0 end) |
  (case when p_kinds && array['모자','썬캡','선캡','캡모자'] then 8 else 0 end) |
  (case when p_kinds && array['가방','백팩','배낭'] then 16 else 0 end) |
  (case when p_kinds && array['카드','신용','체크카드','교통카드'] then 32 else 0 end) as mask
 ) select array_agg(v) from generate_series(0,63) v,wanted w
 where coalesce(cardinality(p_kinds),0)=0 or (v&w.mask)<>0
$$;
revoke all on function public.search_kind_mask(text),public.allowed_search_kind_masks(text[]) from public,anon,authenticated;
grant execute on function public.search_kind_mask(text),public.allowed_search_kind_masks(text[]) to service_role;

create index found_item_search_documents_kind
 on public.found_item_search_documents(storage_location_id,public.search_kind_mask(search_text),item_id)
 include(content_hash);
analyze public.found_item_search_documents;

create or replace function public.search_found_items_semantic_fast(
 p_location_ids bigint[],p_embedding extensions.vector(1024),p_kinds text[] default null)
returns table(item_id bigint,similarity double precision)
language sql stable security invoker set search_path='' set statement_timeout='4s' set jit='off'
set effective_io_concurrency='64' as $$
 with candidates as materialized (
  select d.item_id,d.storage_location_id,d.content_hash
  from public.found_item_search_documents d
  where d.storage_location_id=any(p_location_ids) and cardinality(p_location_ids) between 1 and 300
   and public.search_kind_mask(d.search_text)=any(public.allowed_search_kind_masks(p_kinds))
 ), distances as materialized (
  select e.item_id,e.storage_location_id,e.content_hash,
   coalesce(e.full_embedding,e.half_embedding::extensions.vector(1024))
    operator(extensions.<=>) p_embedding as distance
  from public.found_item_search_vectors e
  where e.item_id=any(array(select c.item_id from candidates c))
   and e.storage_location_id=any(p_location_ids)
 ) select v.item_id,1-v.distance from distances v join candidates c
 on c.item_id=v.item_id and c.content_hash=v.content_hash and c.storage_location_id=v.storage_location_id
 where v.distance<=0.45 order by v.distance,v.item_id desc limit 200
$$;

create or replace function public.search_found_item_candidates(p_location_ids bigint[],p_terms text[],p_kinds text[] default null)
returns jsonb language sql stable security invoker set search_path='' set statement_timeout='4s' set jit='off'
set effective_io_concurrency='64' as $$
 with matched as materialized (
  select d.item_id from public.found_item_search_documents d
  where d.storage_location_id=any(p_location_ids) and cardinality(p_location_ids) between 1 and 300
   and public.search_kind_mask(d.search_text)=any(public.allowed_search_kind_masks(p_kinds))
   and exists(select 1 from unnest(p_terms) t(term) where term<>'' and strpos(d.search_text,lower(t.term))>0)
 ) select jsonb_build_object('candidate_count',(
  select count(*) from public.found_item_search_documents d
  where d.storage_location_id=any(p_location_ids) and cardinality(p_location_ids) between 1 and 300),
  'items',coalesce((select jsonb_agg(d.detail) from public.found_item_search_documents d
   where d.item_id=any(array(select m.item_id from matched m))),'[]'::jsonb))
$$;
