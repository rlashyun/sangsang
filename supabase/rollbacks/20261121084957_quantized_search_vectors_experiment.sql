-- 되돌리기: 20261121084957_quantized_search_vectors_experiment (수동 실행 전용, 자동 실행 금지)
-- 2026-11-21 김성현 검색 실험의 1·2비트 양자화 사본과 실험 함수만 지운다. 원본 found_item_search_vectors는 건드리지 않는다.
-- 용량: 1비트 약 185MB, 2비트 약 321MB(2026-11-21 측정, content_hash 포함). DROP은 파일을 바로 지워 공간이 즉시 돌아온다.
set lock_timeout = '3s';
drop function if exists public.exp_semantic_2bit(bigint[], extensions.halfvec, float8, int, int);
drop function if exists public.exp_semantic_1bit(bigint[], extensions.halfvec, float8, int, int);
drop function if exists public.exp_semantic_half(bigint[], extensions.halfvec, float8, int);
drop function if exists public.exp_quantize_2bit(extensions.halfvec);
drop table if exists public.found_item_search_vectors_2bit;
drop table if exists public.found_item_search_vectors_1bit;
drop table if exists public.search_vector_2bit_params;
delete from supabase_migrations.schema_migrations where version = '20261121084957';
notify pgrst, 'reload schema';
