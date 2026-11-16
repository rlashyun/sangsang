-- PRD 4.1 · 4.5 · 5 — 기존 BGE-M3 저장 형식을 재현하고 기관 범위 안에서만 검색한다.
create extension if not exists vector with schema extensions;
create extension if not exists pgcrypto with schema extensions;

create table if not exists public.found_item_embeddings_bge_m3 (
  item_id bigint primary key references public.found_items(id) on delete cascade,
  embedding extensions.vector(1024) not null,
  model text not null default 'baai/bge-m3' check (model = 'baai/bge-m3'),
  provider text not null default 'deepinfra' check (provider = 'deepinfra'),
  input_version text not null default 'normalized_search_text_or_item_name:v1',
  content_hash text not null,
  vector_source text not null,
  is_current boolean not null default true,
  embedded_at timestamptz not null default now()
);
alter table public.found_item_embeddings_bge_m3 enable row level security;
revoke all on public.found_item_embeddings_bge_m3 from public, anon, authenticated;
grant select, insert, update, delete on public.found_item_embeddings_bge_m3 to service_role;

create or replace function public.bge_m3_input(normalized text, name text)
returns text language sql immutable parallel safe set search_path = '' as $$
  select coalesce(nullif(regexp_replace(normalized, '^\s+|\s+$', '', 'g'), ''),
                  nullif(regexp_replace(name, '^\s+|\s+$', '', 'g'), ''))
$$;

create or replace function public.upsert_bge_m3_embeddings(records jsonb)
returns table(item_id bigint) language sql set search_path = ''
set statement_timeout = '45s' set work_mem = '16MB' set jit = 'off' as $$
  with supplied as (
    select * from jsonb_to_recordset(records)
      as r(item_id bigint, content_hash text, embedding text, vector_source text)
  ), current_rows as materialized (
    select f.id, s.content_hash, s.embedding, s.vector_source
    from public.found_items f join supplied s on s.item_id = f.id
    where encode(extensions.digest(public.bge_m3_input(f.normalized_search_text, f.item_name), 'sha256'), 'hex') = s.content_hash
    for share of f
  )
  insert into public.found_item_embeddings_bge_m3 as target(item_id, content_hash, embedding, vector_source)
  select id, content_hash, embedding::extensions.vector(1024), vector_source from current_rows
  on conflict(item_id) do update set content_hash = excluded.content_hash,
    embedding = excluded.embedding, vector_source = excluded.vector_source, is_current = true, embedded_at = now()
  where target.content_hash <> excluded.content_hash or not target.is_current
  returning target.item_id
$$;

create or replace function public.search_found_items_semantic(
  p_location_ids bigint[], p_embedding extensions.vector(1024)
)
returns table(item_id bigint, similarity double precision)
language sql stable security invoker set search_path = ''
set statement_timeout = '4s' set jit = 'off' as $$
  select f.id, 1 - (e.embedding operator(extensions.<=>) p_embedding) as similarity
  from public.found_items f
  join public.found_item_embeddings_bge_m3 e on e.item_id = f.id
  where cardinality(p_location_ids) between 1 and 300
    and f.storage_location_id = any(p_location_ids)
    and f.location_match_status = 'matched'
    and e.is_current and e.model = 'baai/bge-m3' and e.provider = 'deepinfra'
    and e.input_version = 'normalized_search_text_or_item_name:v1'
    and e.content_hash = encode(extensions.digest(public.bge_m3_input(f.normalized_search_text, f.item_name), 'sha256'), 'hex')
    and 1 - (e.embedding operator(extensions.<=>) p_embedding) >= 0.55
  order by e.embedding operator(extensions.<=>) p_embedding, f.id desc
  limit 200
$$;

revoke all on function public.bge_m3_input(text, text) from public, anon, authenticated;
revoke all on function public.upsert_bge_m3_embeddings(jsonb) from public, anon, authenticated;
revoke all on function public.search_found_items_semantic(bigint[], extensions.vector) from public, anon, authenticated;
grant execute on function public.bge_m3_input(text, text) to service_role;
grant execute on function public.upsert_bge_m3_embeddings(jsonb) to service_role;
grant execute on function public.search_found_items_semantic(bigint[], extensions.vector) to service_role;
