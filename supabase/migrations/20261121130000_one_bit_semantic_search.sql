-- PRD 4.1 / 4.5 / 5 — 2026-11-21 김성현 결정: 텍스트 검색 확정안(1비트 후보 → 원본 재채점 + BM25 가중합).
-- 근거 실험은 docs/measurements.md "반경 전체 의미 검색 — 1·2비트 양자화 속도와 디스크 사고" 절.
--   - 반경 안 전체 물품을 1비트(부호) 벡터의 해밍 거리로 비교해 가까운 500건만 남기고,
--     그 500건만 원본 halfvec(found_item_embeddings_bge_m3)으로 코사인을 다시 계산한다.
--     원본 정확 검색 상위 100건과 99.95% 같고, 서울역 1km 정답 Recall@30도 같았다.
--   - 검색용 원본 사본 found_item_search_vectors(1,753MB)는 더 쓰지 않는다. 재채점·키워드 재정렬은
--     원본 테이블에서 읽는다. 2비트 표와 실험 함수(exp_*)도 지운다.
--   - 최종 점수(앱): 0.75·BM25 + 0.25·(cos−0.60)/0.40. 원본 벡터 열은 storage plain으로 바꾼다.
-- 적용: GitHub 연동(Deploy to production)으로 main 병합 시 적용된다. 운영 최신 버전 20261121120000 뒤 번호다.
-- 일일 동기화: 임베딩이 저장되거나(embeddings_sync_search) 물품 위치가 바뀌면(found_items_sync_search)
--   refresh_search_vector가 1비트 표를 채우고 지운다. 원본 사본 대신 1비트 표만 유지한다.
-- 되돌리기: supabase/rollbacks/ 아래 같은 버전·이름의 SQL을 수동 실행한다(원본 사본은 다시 채워야 한다).
set lock_timeout = '5s';
set statement_timeout = '120s';

-- 1) 실험 전용 객체 삭제
drop function if exists public.exp_semantic_half(bigint[], extensions.halfvec, float8, int);
drop function if exists public.exp_semantic_1bit(bigint[], extensions.halfvec, float8, int, int);
drop function if exists public.exp_semantic_2bit(bigint[], extensions.halfvec, float8, int, int);
drop function if exists public.exp_quantize_2bit(extensions.halfvec);
drop table if exists public.found_item_search_vectors_2bit;
drop table if exists public.search_vector_2bit_params;

-- 2) 1비트 표를 원본 임베딩에 직접 묶는다(물품·임베딩 삭제 시 함께 지워진다).
alter table public.found_item_search_vectors_1bit drop constraint found_item_search_vectors_1bit_item_id_fkey;
alter table public.found_item_search_vectors_1bit add constraint found_item_search_vectors_1bit_item_id_fkey
  foreign key (item_id) references public.found_item_embeddings_bge_m3(item_id) on delete cascade;

-- 3) 동기화: 원본 사본 대신 1비트 표를 갱신한다. 값이 같으면 다시 쓰지 않는다.
create or replace function public.refresh_search_vector(p_item_id bigint) returns void
language plpgsql security invoker set search_path = '' as $$
declare f public.found_items; e public.found_item_embeddings_bge_m3;
begin
 select * into f from public.found_items where id = p_item_id;
 select * into e from public.found_item_embeddings_bge_m3 where item_id = p_item_id;
 if f.location_match_status = 'matched' and f.storage_location_id is not null
 and e.is_current and e.input_version = 'color_ko_item_name:v2'
 and e.model = 'baai/bge-m3' and e.provider = 'deepinfra' then
  insert into public.found_item_search_vectors_1bit(item_id, storage_location_id, content_hash, embedding)
  values (e.item_id, f.storage_location_id, e.content_hash, extensions.binary_quantize(e.embedding)::bit(1024))
  on conflict (item_id) do update set storage_location_id = excluded.storage_location_id,
   content_hash = excluded.content_hash, embedding = excluded.embedding
  where (public.found_item_search_vectors_1bit.storage_location_id, public.found_item_search_vectors_1bit.content_hash,
         public.found_item_search_vectors_1bit.embedding)
    is distinct from (excluded.storage_location_id, excluded.content_hash, excluded.embedding);
 else
  delete from public.found_item_search_vectors_1bit where item_id = p_item_id;
 end if;
end $$;

-- 4) 키워드 상위 200건 재정렬: 원본 임베딩에서 읽는다. 1비트 행이 있어야(위치 일치·현재 버전) 벡터를 준다.
create or replace function public.keyword_rerank_vectors(p_location_ids bigint[], p_item_ids bigint[])
returns table(item_id bigint, detail jsonb, vector text)
language sql stable security invoker set search_path = '' set jit = 'off' set statement_timeout = '1500ms' as $$
 select d.item_id, d.detail,
  case when e.item_id is not null then encode(extensions.vector_send(e.embedding::extensions.vector(1024)), 'base64') end
 from public.found_item_search_documents d
 left join public.found_item_search_vectors_1bit b on b.item_id = d.item_id and b.storage_location_id = d.storage_location_id
 left join public.found_item_embeddings_bge_m3 e on e.item_id = b.item_id
 where d.item_id = any(p_item_ids) and d.storage_location_id = any(p_location_ids)
  and cardinality(p_item_ids) between 1 and 200 and cardinality(p_location_ids) between 1 and 300
$$;

-- 5) 반경 전체 의미 검색: 1비트 해밍 상위 p_rescore건 → 원본 코사인 재채점 → p_min 이상 상위 p_limit건.
create function public.semantic_found_item_candidates(p_location_ids bigint[], p_query extensions.halfvec,
  p_min float8 default 0.6, p_limit int default 100, p_rescore int default 500)
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
  select b.item_id from public.found_item_search_vectors_1bit b
  where b.storage_location_id = any(p_location_ids)
  order by bit_count(b.embedding # q), b.item_id desc limit p_rescore
 ), s as materialized (
  select e.item_id, (1 - (e.embedding operator(extensions.<=>) p_query))::float8 sim
  from c join public.found_item_embeddings_bge_m3 e on e.item_id = c.item_id
 ), top as materialized (
  select s.item_id, s.sim from s where s.sim >= p_min order by s.sim desc, s.item_id desc limit p_limit
 )
 select t.item_id, t.sim, d.detail from top t
 join public.found_item_search_documents d on d.item_id = t.item_id and d.storage_location_id = any(p_location_ids)
 order by t.sim desc, t.item_id desc;
end $$;

-- 6) 더 쓰지 않는 원본 사본 삭제(CASCADE 없이: 남은 의존이 있으면 실패해야 한다).
drop table public.found_item_search_vectors;

-- 7) 원본 벡터를 TOAST 대신 행 안에 저장한다(사본이 빨랐던 이유가 storage plain이었다).
--    카탈로그만 바뀌므로 즉시 끝나고, 이후 저장되는 벡터부터 행 안에 들어간다.
--    기존 61만 행은 WAL을 보며 supabase/maintenance/inline_embedding_storage.sql로 나눠 옮긴다.
alter table public.found_item_embeddings_bge_m3 alter column embedding set storage plain;

-- 8) 지운 표를 읽는 함수가 남지 않았는지 확인한다.
do $$ begin
 if exists (select 1 from pg_proc where pronamespace = 'public'::regnamespace and prokind = 'f'
  and regexp_replace(pg_get_functiondef(oid), 'found_item_search_vectors_1bit', '', 'g') like '%found_item_search_vectors%') then
  raise exception 'function still references dropped found_item_search_vectors';
 end if;
end $$;

revoke all on function public.semantic_found_item_candidates(bigint[], extensions.halfvec, float8, int, int)
  from public, anon, authenticated;
grant execute on function public.semantic_found_item_candidates(bigint[], extensions.halfvec, float8, int, int) to service_role;
revoke all on function public.refresh_search_vector(bigint), public.keyword_rerank_vectors(bigint[], bigint[])
  from public, anon, authenticated;
grant execute on function public.refresh_search_vector(bigint), public.keyword_rerank_vectors(bigint[], bigint[]) to service_role;

notify pgrst, 'reload schema';
reset lock_timeout;
reset statement_timeout;
