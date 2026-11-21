-- 되돌리기: one_bit_semantic_search. 수동 실행한다.
-- 원본 사본 found_item_search_vectors를 다시 만들고 원본 임베딩에서 채운다(약 1.7GB, 61만 행).
-- 주의: 디스크 여유와 WAL을 먼저 확인하고, 채우기는 item_id 구간을 나눠 한 번에 하나씩 실행한다
-- (2026-11-21 디스크 사고 — 대량 INSERT 동시 실행 금지). 2비트 표와 exp_* 실험 함수는 복원하지 않는다.
set lock_timeout = '5s';

create table public.found_item_search_vectors (
  item_id bigint primary key references public.found_item_embeddings_bge_m3(item_id) on delete cascade,
  storage_location_id bigint,
  content_hash text not null,
  half_embedding extensions.halfvec(1024) not null
);
alter table public.found_item_search_vectors alter column half_embedding set storage plain;
create index found_item_search_vectors_location on public.found_item_search_vectors(storage_location_id, item_id);
alter table public.found_item_search_vectors enable row level security;
revoke all on public.found_item_search_vectors from public, anon, authenticated;
grant select, insert, update, delete on public.found_item_search_vectors to service_role;

-- 1비트 표는 원본 사본을 따라 지워지던 이전 관계로 되돌린다(채우기 뒤에 실행).
-- alter table public.found_item_search_vectors_1bit drop constraint found_item_search_vectors_1bit_item_id_fkey;
-- alter table public.found_item_search_vectors_1bit add constraint found_item_search_vectors_1bit_item_id_fkey
--   foreign key (item_id) references public.found_item_search_vectors(item_id) on delete cascade;

create or replace function public.refresh_search_vector(p_item_id bigint) returns void
language plpgsql security invoker set search_path = '' as $$
declare f public.found_items; e public.found_item_embeddings_bge_m3;
begin
 select * into f from public.found_items where id = p_item_id;
 select * into e from public.found_item_embeddings_bge_m3 where item_id = p_item_id;
 if f.location_match_status = 'matched' and f.storage_location_id is not null
 and e.is_current and e.input_version = 'color_ko_item_name:v2'
 and e.model = 'baai/bge-m3' and e.provider = 'deepinfra' then
  insert into public.found_item_search_vectors(item_id, storage_location_id, content_hash, half_embedding)
  values (e.item_id, f.storage_location_id, e.content_hash, e.embedding)
  on conflict (item_id) do update set storage_location_id = excluded.storage_location_id,
   content_hash = excluded.content_hash, half_embedding = excluded.half_embedding;
  insert into public.found_item_search_vectors_1bit(item_id, storage_location_id, content_hash, embedding)
  values (e.item_id, f.storage_location_id, e.content_hash, extensions.binary_quantize(e.embedding)::bit(1024))
  on conflict (item_id) do update set storage_location_id = excluded.storage_location_id,
   content_hash = excluded.content_hash, embedding = excluded.embedding;
 else
  delete from public.found_item_search_vectors_1bit where item_id = p_item_id;
  delete from public.found_item_search_vectors where item_id = p_item_id;
 end if;
end $$;

create or replace function public.keyword_rerank_vectors(p_location_ids bigint[], p_item_ids bigint[])
returns table(item_id bigint, detail jsonb, vector text)
language sql stable security invoker set search_path = '' set jit = 'off' set statement_timeout = '1500ms' as $$
 select d.item_id, d.detail,
  case when e.item_id is not null then encode(extensions.vector_send(e.half_embedding::extensions.vector(1024)), 'base64') end
 from public.found_item_search_documents d left join public.found_item_search_vectors e
  on e.item_id = d.item_id and e.storage_location_id = d.storage_location_id
 where d.item_id = any(p_item_ids) and d.storage_location_id = any(p_location_ids)
  and cardinality(p_item_ids) between 1 and 200 and cardinality(p_location_ids) between 1 and 300
$$;

drop function if exists public.semantic_found_item_candidates(bigint[], extensions.halfvec, float8, int, int);

-- 저장 방식은 되돌리지 않아도 검색이 동작한다. 되돌리려면 아래 뒤 기존 행을 나눠 다시 써야 TOAST로 옮겨진다.
-- alter table public.found_item_embeddings_bge_m3 alter column embedding set storage external;

-- 채우기(구간마다 하나씩, 예: 10만 건 단위):
-- insert into public.found_item_search_vectors(item_id, storage_location_id, content_hash, half_embedding)
-- select e.item_id, f.storage_location_id, e.content_hash, e.embedding
-- from public.found_item_embeddings_bge_m3 e join public.found_items f on f.id = e.item_id
-- where e.item_id > :lo and e.item_id <= :hi and f.location_match_status = 'matched' and f.storage_location_id is not null
--   and e.is_current and e.input_version = 'color_ko_item_name:v2' and e.model = 'baai/bge-m3' and e.provider = 'deepinfra'
-- on conflict (item_id) do nothing;

revoke all on function public.refresh_search_vector(bigint), public.keyword_rerank_vectors(bigint[], bigint[])
  from public, anon, authenticated;
grant execute on function public.refresh_search_vector(bigint), public.keyword_rerank_vectors(bigint[], bigint[]) to service_role;
-- delete from supabase_migrations.schema_migrations where version = '<이 마이그레이션 버전>';
notify pgrst, 'reload schema';
