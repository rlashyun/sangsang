-- 되돌리기: gemini2_embeddings. 수동 실행한다. 적재한 Gemini 벡터도 함께 지워진다(다시 적재해야 한다).
-- 기존 BGE 검색 경로는 이 마이그레이션이 바꾸지 않았으므로 되돌릴 것이 없다.
set lock_timeout = '5s';

drop trigger if exists found_items_sync_gemini2_vector on public.found_items;
drop function if exists public.semantic_found_item_candidates_gemini2(bigint[], extensions.halfvec, float8, int, int);
drop function if exists public.upsert_gemini2_embeddings(jsonb);
drop table if exists public.found_item_search_vectors_gemini2_1bit;
drop table if exists public.found_item_embeddings_gemini2;
drop function if exists public.sync_gemini2_vector_location();
drop function if exists public.sync_gemini2_search_vector();
drop function if exists public.refresh_gemini2_search_vector(bigint);

-- delete from supabase_migrations.schema_migrations where version = '20261122100000';
notify pgrst, 'reload schema';
reset lock_timeout;
