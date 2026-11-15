-- PRD 4.5 / 5 — user-approved v2 rebuild. Item/source text is never rewritten.
-- DESTRUCTIVE: clears both embedding tables, not found_items/search_documents.
set lock_timeout = '3s';
set statement_timeout = '180s';

create or replace function public.item_embedding_input(color text, name text)
returns text language sql immutable parallel safe security invoker set search_path='' as $$
 with c as (
  select case
   when color ~ '\([^)]*[가-힣][^)]*\)' then substring(color from '\(([^)]*[가-힣][^)]*)\)')
   when color ~ '[가-힣]' and color !~ '[A-Za-z]' then color
   else '' end as value
 ) select btrim(regexp_replace(coalesce(c.value,'') || ' ' || coalesce(name,''), '\s+', ' ', 'g')) from c
$$;
revoke all on function public.item_embedding_input(text,text) from public,anon,authenticated;
grant execute on function public.item_embedding_input(text,text) to service_role;

alter table public.found_items add column embedding_input text
 generated always as (public.item_embedding_input(color_name,item_name)) stored;

-- Truncate explicitly, without CASCADE: a future dependency must cause failure.
truncate table public.found_item_search_vectors, public.found_item_embeddings_bge_m3;
drop trigger if exists found_items_invalidate_bge_m3 on public.found_items;
drop function if exists public.invalidate_bge_m3_embedding();

alter table public.found_item_embeddings_bge_m3
 drop constraint if exists found_item_embeddings_bge_m3_embedding_check,
 drop constraint if exists found_item_embeddings_bge_m3_input_version_check,
 drop constraint if exists found_item_embeddings_bge_m3_vector_source_check;
alter table public.found_item_embeddings_bge_m3
 alter column embedding type extensions.halfvec(1024) using embedding::extensions.halfvec(1024),
 alter column input_version set default 'color_ko_item_name:v2',
 add column embedded_input text not null;
alter table public.found_item_embeddings_bge_m3
 add constraint found_item_embeddings_bge_m3_embedding_check check(extensions.vector_norm(embedding::extensions.vector(1024))>0),
 add constraint found_item_embeddings_bge_m3_input_version_check check(input_version='color_ko_item_name:v2'),
 add constraint found_item_embeddings_bge_m3_vector_source_check check(vector_source='openrouter_half_v2');
alter table public.found_item_embeddings_bge_m3 add constraint embedding_snapshot_hash_valid
 check (content_hash = encode(extensions.digest(embedded_input,'sha256'),'hex') and embedded_input <> '');
alter table public.found_item_search_vectors drop constraint found_item_search_vectors_check;
-- PL/pgSQL/text-body functions are replaced below before they can run again.
alter table public.found_item_search_vectors drop column full_embedding;
alter table public.found_item_search_vectors alter column half_embedding set not null;

create or replace function public.refresh_search_vector(p_item_id bigint) returns void
language plpgsql security invoker set search_path='' as $$
declare f public.found_items; e public.found_item_embeddings_bge_m3;
begin
 select * into f from public.found_items where id=p_item_id;
 select * into e from public.found_item_embeddings_bge_m3 where item_id=p_item_id;
 if f.location_match_status='matched' and f.storage_location_id is not null
 and e.is_current and e.input_version='color_ko_item_name:v2'
 and e.model='baai/bge-m3' and e.provider='deepinfra' then
  insert into public.found_item_search_vectors(item_id,storage_location_id,content_hash,half_embedding)
  values(e.item_id,f.storage_location_id,e.content_hash,e.embedding)
  on conflict(item_id) do update set storage_location_id=excluded.storage_location_id,
   content_hash=excluded.content_hash,half_embedding=excluded.half_embedding;
 else
  delete from public.found_item_search_vectors where item_id=p_item_id;
 end if;
end $$;

create or replace function public.sync_search_document() returns trigger
language plpgsql security invoker set search_path='' as $$
declare h text;
begin
 h := encode(extensions.digest(public.bge_m3_input(new.normalized_search_text,new.item_name),'sha256'),'hex');
 if new.location_match_status='matched' and new.storage_location_id is not null then
  insert into public.found_item_search_documents(item_id,storage_location_id,content_hash,search_text,detail)
  values(new.id,new.storage_location_id,h,
   lower(concat_ws(' ',new.item_name,new.raw_category_name,new.color_name,new.description)),public.search_document(new))
  on conflict(item_id) do update set storage_location_id=excluded.storage_location_id,
   content_hash=excluded.content_hash,search_text=excluded.search_text,detail=excluded.detail;
 else
  delete from public.found_item_search_documents where item_id=new.id;
 end if;
 -- Deliberately preserve an existing vector after name/color edits (user policy).
 perform public.refresh_search_vector(new.id);
 return new;
end $$;

create or replace function public.upsert_bge_m3_embeddings(records jsonb)
returns table(item_id bigint) language sql security invoker set search_path=''
set statement_timeout='30s' set jit='off' as $$
 with supplied as (
  select * from jsonb_to_recordset(records)
   as r(item_id bigint,content_hash text,embedding text,vector_source text,embedded_input text)
 ), current_rows as materialized (
  select f.id,s.* from public.found_items f join supplied s on s.item_id=f.id
  where f.embedding_input<>'' and f.embedding_input=s.embedded_input
   and s.content_hash=encode(extensions.digest(s.embedded_input,'sha256'),'hex')
  for share of f
 ) insert into public.found_item_embeddings_bge_m3
  (item_id,embedding,content_hash,vector_source,embedded_input,input_version)
 select id,embedding::extensions.halfvec(1024),content_hash,vector_source,embedded_input,'color_ko_item_name:v2'
 from current_rows
 on conflict(item_id) do nothing
 returning found_item_embeddings_bge_m3.item_id
$$;

create or replace function public.keyword_rerank_vectors(p_location_ids bigint[],p_item_ids bigint[])
returns table(item_id bigint,detail jsonb,vector text)
language sql stable security invoker set search_path='' set jit='off' set statement_timeout='1500ms' as $$
 select d.item_id,d.detail,
  case when e.item_id is not null then encode(extensions.vector_send(e.half_embedding::extensions.vector(1024)),'base64') end
 from public.found_item_search_documents d left join public.found_item_search_vectors e
  on e.item_id=d.item_id and e.storage_location_id=d.storage_location_id
 where d.item_id=any(p_item_ids) and d.storage_location_id=any(p_location_ids)
  and cardinality(p_item_ids) between 1 and 200 and cardinality(p_location_ids) between 1 and 300
$$;

-- Keep previous RPC contracts callable, without retaining obsolete full/hash gates.
do $migration$
declare p record; body text;
begin
 for p in select oid,proname from pg_proc where pronamespace='public'::regnamespace and prokind='f'
 and proname in ('read_found_item_vector_scores','rerank_found_item_candidates','search_found_items_semantic_fast','search_found_items_semantic') loop
  body := pg_get_functiondef(p.oid);
  body := replace(body,'coalesce(e.full_embedding,e.half_embedding::extensions.vector(1024))','e.half_embedding::extensions.vector(1024)');
  body := replace(body,'e.content_hash=d.content_hash and ','');
  body := replace(body,'and c.content_hash=v.content_hash','');
  body := replace(body,'and e.content_hash=d.content_hash','');
  if p.proname='search_found_items_semantic' then
   body := replace(body,'e.embedding operator','e.embedding::extensions.vector(1024) operator');
   body := replace(body,'normalized_search_text_or_item_name:v1','color_ko_item_name:v2');
   body := regexp_replace(body,'and e.content_hash = encode\(extensions.digest\(public.bge_m3_input\(f.normalized_search_text, f.item_name\), ''sha256''\), ''hex''\)','');
  end if;
  execute body;
 end loop;
end $migration$;

create or replace function public.seed_found_item_search(p_after bigint,p_limit integer default 2000)
returns jsonb language plpgsql security invoker set search_path='' set statement_timeout='45s' as $$
declare f public.found_items; last_id bigint:=p_after; n integer:=0;
begin
 for f in select * from public.found_items where id>p_after order by id
  limit least(greatest(p_limit,1),5000) for share loop
  if f.location_match_status='matched' and f.storage_location_id is not null then
   insert into public.found_item_search_documents(item_id,storage_location_id,content_hash,search_text,detail)
   values(f.id,f.storage_location_id,encode(extensions.digest(public.bge_m3_input(f.normalized_search_text,f.item_name),'sha256'),'hex'),
    lower(concat_ws(' ',f.item_name,f.raw_category_name,f.color_name,f.description)),public.search_document(f))
   on conflict do nothing;
  end if;
  perform public.refresh_search_vector(f.id);
  last_id:=f.id; n:=n+1;
 end loop;
 return jsonb_build_object('last_id',last_id,'read',n);
end $$;

-- No function may accidentally preserve the removed column's read path.
do $$ begin
 if exists(select 1 from pg_proc where pronamespace='public'::regnamespace and prokind='f'
  and pg_get_functiondef(oid) like '%full_embedding%') then
  raise exception 'obsolete full_embedding function reference';
 end if;
end $$;

notify pgrst,'reload schema';
reset lock_timeout;
reset statement_timeout;
