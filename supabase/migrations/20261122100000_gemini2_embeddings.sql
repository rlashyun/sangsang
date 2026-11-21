-- PRD 4.5 / 5 — 2026-11-22 김성현 결정: 물품 임베딩을 Gemini Embedding 2로 바꾸기 위한 저장소(1단계).
-- 근거 실험: multimodal_v2_1121(서울역 1km 27,747건, 질의 100개 50/50). Gemini TASK 형식이 BGE-M3보다 Recall@30 +9.03%p.
--   - 모델 google/gemini-embedding-2(OpenRouter → Google AI Studio 고정), 1024차원(이미 L2 정규화됨).
--   - 물품 입력 'title: none | text: {마스킹한 embedding_input}'. 실제 사진이 있으면 같은 입력에 사진을 함께 넣어 벡터 1개.
--     질의 입력은 'task: search result | query: {질의}'(앱 전환 단계에서 쓴다).
--   - 0단계 확인(2026-11-22): 글+사진 한 입력 → 벡터 1개·1024차원, 일괄 요청과 단건 요청 벡터 동일, 사진 1장 258토큰.
-- 이번 단계는 저장소만 만든다. 앱 검색과 일일 동기화(크론 임베딩)는 바꾸지 않는다.
--   - 벡터는 로컬 적재 도구가 upsert_gemini2_embeddings로 채운다. 검색 전환 전까지 아무도 읽지 않는다.
--   - Gemini 1비트 표는 Gemini 벡터 저장·물품 위치 변경 때 refresh_gemini2_search_vector로 맞춘다.
--     기존 BGE 경로(refresh_search_vector·sync_search_document)는 건드리지 않는다.
-- 저장 규칙(BGE 표와 같다): embedded_input은 found_items.embedding_input 원문, content_hash는 그 sha256.
--   보낸 글은 privacy 마스킹을 거친 사본이다. 사진은 원본 URL과 원본 바이트 sha256을 함께 둔다(사진이 바뀌면 다시 만든다).
-- 디스크: 행 약 2.4KB(벡터 행 안 저장, TOAST 없음) × 61.6만 ≈ 1.6GB + 1비트 표 약 0.15GB. BGE 표는 적재 완료 뒤 따로 지운다.
-- 되돌리기: supabase/rollbacks/ 아래 같은 버전·이름의 SQL을 수동 실행한다.
set lock_timeout = '5s';

-- 1) Gemini 물품 벡터. 벡터는 처음부터 행 안에 둔다(20261121190000·200000에서 겪은 TOAST 문제 방지).
create table public.found_item_embeddings_gemini2 (
  item_id bigint primary key references public.found_items(id) on delete cascade,
  embedding extensions.halfvec(1024) not null,
  embedded_input text not null,
  content_hash text not null,
  image_url text,
  image_sha256 text,
  model text not null default 'google/gemini-embedding-2',
  provider text not null default 'google-ai-studio',
  input_version text not null default 'gemini2_title_none:v1',
  embedded_at timestamptz not null default now(),
  constraint found_item_embeddings_gemini2_model_check check (model = 'google/gemini-embedding-2'),
  constraint found_item_embeddings_gemini2_provider_check check (provider = 'google-ai-studio'),
  constraint found_item_embeddings_gemini2_input_version_check check (input_version = 'gemini2_title_none:v1'),
  constraint found_item_embeddings_gemini2_hash_check
    check (embedded_input <> '' and content_hash = encode(extensions.digest(embedded_input, 'sha256'), 'hex')),
  constraint found_item_embeddings_gemini2_image_check
    check ((image_url is null) = (image_sha256 is null)
           and (image_url is null or image_url like 'https://%')
           and (image_sha256 is null or image_sha256 ~ '^[a-f0-9]{64}$')),
  constraint found_item_embeddings_gemini2_embedding_check
    check (extensions.vector_norm(embedding::extensions.vector(1024)) > 0)
) with (toast_tuple_target = 4000);
alter table public.found_item_embeddings_gemini2 alter column embedding set storage plain;
alter table public.found_item_embeddings_gemini2 enable row level security;

-- 2) 반경 검색용 1비트 표(BGE 1비트 표와 같은 모양). Gemini 벡터가 지워지면 함께 지워진다.
create table public.found_item_search_vectors_gemini2_1bit (
  item_id bigint primary key references public.found_item_embeddings_gemini2(item_id) on delete cascade,
  storage_location_id bigint not null,
  embedding bit(1024) not null
);
create index found_item_search_vectors_gemini2_1bit_location
  on public.found_item_search_vectors_gemini2_1bit (storage_location_id, item_id);
alter table public.found_item_search_vectors_gemini2_1bit enable row level security;

revoke all on public.found_item_embeddings_gemini2, public.found_item_search_vectors_gemini2_1bit
  from public, anon, authenticated;
grant select, insert, update, delete on public.found_item_embeddings_gemini2, public.found_item_search_vectors_gemini2_1bit
  to service_role;

-- 3) 1비트 표 맞추기: 위치가 확정된 물품만 남긴다. 값이 같으면 다시 쓰지 않는다.
create function public.refresh_gemini2_search_vector(p_item_id bigint) returns void
language plpgsql security invoker set search_path = '' as $$
declare f public.found_items; e public.found_item_embeddings_gemini2;
begin
 select * into f from public.found_items where id = p_item_id;
 select * into e from public.found_item_embeddings_gemini2 where item_id = p_item_id;
 if e.item_id is not null and f.location_match_status = 'matched' and f.storage_location_id is not null then
  insert into public.found_item_search_vectors_gemini2_1bit(item_id, storage_location_id, embedding)
  values (e.item_id, f.storage_location_id, extensions.binary_quantize(e.embedding)::bit(1024))
  on conflict (item_id) do update set storage_location_id = excluded.storage_location_id, embedding = excluded.embedding
  where (public.found_item_search_vectors_gemini2_1bit.storage_location_id, public.found_item_search_vectors_gemini2_1bit.embedding)
    is distinct from (excluded.storage_location_id, excluded.embedding);
 else
  delete from public.found_item_search_vectors_gemini2_1bit where item_id = p_item_id;
 end if;
end $$;

create function public.sync_gemini2_search_vector() returns trigger
language plpgsql security invoker set search_path = '' as $$
begin
 perform public.refresh_gemini2_search_vector(new.item_id);
 return new;
end $$;

create function public.sync_gemini2_vector_location() returns trigger
language plpgsql security invoker set search_path = '' as $$
begin
 perform public.refresh_gemini2_search_vector(new.id);
 return new;
end $$;

create trigger gemini2_embeddings_sync_search
  after insert or update of embedding on public.found_item_embeddings_gemini2
  for each row execute function public.sync_gemini2_search_vector();

-- 물품 위치가 바뀔 때만 동작한다(수집 쓰기 대부분은 위치가 그대로라 건너뛴다).
create trigger found_items_sync_gemini2_vector
  after update of storage_location_id, location_match_status on public.found_items
  for each row when (old.storage_location_id is distinct from new.storage_location_id
                     or old.location_match_status is distinct from new.location_match_status)
  execute function public.sync_gemini2_vector_location();

-- 4) 적재: 로컬 도구가 200건씩 보낸다. 물품의 지금 입력·사진과 같을 때만 저장하고,
--    같은 물품을 다시 보내면(예: 글만 → 글+사진) 바뀐 경우에만 덮어쓴다.
create function public.upsert_gemini2_embeddings(records jsonb)
returns table(item_id bigint)
language plpgsql security invoker set search_path = '' set statement_timeout = '60s' set jit = 'off' as $$
#variable_conflict use_column
begin
 if jsonb_typeof(records) <> 'array' or jsonb_array_length(records) not between 1 and 200 then
  raise exception 'gemini2 batch must have 1..200 records';
 end if;
 return query
 with supplied as (
  select * from jsonb_to_recordset(records)
   as r(item_id bigint, embedded_input text, content_hash text, embedding text, image_url text, image_sha256 text)
 ), current_rows as materialized (
  select s.* from supplied s join public.found_items f on f.id = s.item_id
  where f.embedding_input <> '' and f.embedding_input = s.embedded_input
   and s.content_hash = encode(extensions.digest(s.embedded_input, 'sha256'), 'hex')
   and (s.image_url is null or s.image_url = f.image_url)
  for share of f
 )
 insert into public.found_item_embeddings_gemini2 as g
  (item_id, embedding, embedded_input, content_hash, image_url, image_sha256)
 select c.item_id, c.embedding::extensions.halfvec(1024), c.embedded_input, c.content_hash, c.image_url, c.image_sha256
 from current_rows c
 on conflict on constraint found_item_embeddings_gemini2_pkey do update
  set embedding = excluded.embedding, embedded_input = excluded.embedded_input, content_hash = excluded.content_hash,
      image_url = excluded.image_url, image_sha256 = excluded.image_sha256, embedded_at = now()
  where (g.content_hash, g.image_sha256, g.embedding::text) is distinct from
        (excluded.content_hash, excluded.image_sha256, excluded.embedding::text)
 returning g.item_id;
end $$;

-- 5) 반경 전체 의미 검색(Gemini). BGE 함수와 같은 방식: 1비트 해밍 상위 p_rescore → halfvec 코사인 → p_min 이상 상위 p_limit.
--    검색 전환 단계 전까지 앱은 부르지 않는다. 기본 임계값 0.525는 텍스트 실험 보정값이며 사진 포함 상태에서 다시 확인한다.
create function public.semantic_found_item_candidates_gemini2(p_location_ids bigint[], p_query extensions.halfvec,
  p_min float8 default 0.525, p_limit int default 200, p_rescore int default 500)
returns table(item_id bigint, similarity float8, detail jsonb)
language plpgsql stable security invoker set search_path = '' set jit = 'off' set statement_timeout = '2500ms' as $$
declare q bit(1024);
begin
 if cardinality(p_location_ids) is null or cardinality(p_location_ids) not between 1 and 300
 or p_limit not between 1 and 200 or p_rescore not between p_limit and 2000
 or extensions.vector_dims(p_query) <> 1024 then
  raise exception 'invalid semantic search arguments';
 end if;
 q := extensions.binary_quantize(p_query)::bit(1024);
 return query
 with c as materialized (
  select b.item_id from public.found_item_search_vectors_gemini2_1bit b
  where b.storage_location_id = any(p_location_ids)
  order by bit_count(b.embedding # q), b.item_id desc limit p_rescore
 ), s as materialized (
  select e.item_id, (1 - (e.embedding operator(extensions.<=>) p_query))::float8 sim
  from c join public.found_item_embeddings_gemini2 e on e.item_id = c.item_id
 ), top as materialized (
  select s.item_id, s.sim from s where s.sim >= p_min order by s.sim desc, s.item_id desc limit p_limit
 )
 select t.item_id, t.sim, d.detail from top t
 join public.found_item_search_documents d on d.item_id = t.item_id and d.storage_location_id = any(p_location_ids)
 order by t.sim desc, t.item_id desc;
end $$;

revoke all on function public.refresh_gemini2_search_vector(bigint), public.sync_gemini2_search_vector(),
  public.sync_gemini2_vector_location(), public.upsert_gemini2_embeddings(jsonb),
  public.semantic_found_item_candidates_gemini2(bigint[], extensions.halfvec, float8, int, int)
  from public, anon, authenticated;
grant execute on function public.refresh_gemini2_search_vector(bigint), public.upsert_gemini2_embeddings(jsonb),
  public.semantic_found_item_candidates_gemini2(bigint[], extensions.halfvec, float8, int, int) to service_role;

notify pgrst, 'reload schema';
reset lock_timeout;
