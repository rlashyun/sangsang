-- PRD 5 — 2026-11-21 DB 용량 정리. 앱·크론·DB 함수·pg_cron 어디서도 쓰지 않는 검색 색인 2개와 옛 검색 함수 6개를 지운다.
-- 근거(2026-11-21 운영 확인): 앱이 부르는 검색 RPC는 keyword_found_item_candidates(2인자)·keyword_rerank_vectors·
-- search_found_item_details뿐이고, 아래 함수는 저장소 전체(원격 브랜치 44개 포함)와 다른 DB 함수·크론에서 호출이 없다.
-- - found_item_search_documents_kind (107MB): kind_mask 필터용. 20261120120000 이후 키워드 후보는 kind_mask를 쓰지 않으며,
--   기관 범위 조회는 found_item_search_documents_location(storage_location_id,item_id)을 쓴다(EXPLAIN 확인).
-- - search_engine_matched_location_id_idx (24MB): 저장소 밖 검색 실험(search_engine/optimize_exact.sql)이 만든 색인. 생성 후 사용 0회.
-- 그대로 두는 것: kind_mask 열과 search_kind_mask(text) — kind_mask는 search_kind_mask(search_text)로 계산되는 생성 열이다.
-- 되돌리기: supabase/rollbacks/20261121000000_drop_unused_search_objects.sql을 수동 실행한다.
-- 20261120120000 되돌리기(3인자 함수 복원)는 allowed_search_kind_masks가 필요하므로 이 파일의 되돌리기를 먼저 실행한다.
set local lock_timeout = '5s';

drop index if exists public.found_item_search_documents_kind;
drop index if exists public.search_engine_matched_location_id_idx;

drop function if exists public.search_found_items_semantic_fast(bigint[],extensions.vector,text[]);
drop function if exists public.search_found_item_candidates(bigint[],text[],text[]);
drop function if exists public.rerank_found_item_candidates(bigint[],bigint[],extensions.vector);
drop function if exists public.read_found_item_vector_scores(bigint[],bigint[],extensions.vector);
drop function if exists public.search_found_items_semantic(bigint[],extensions.vector);
drop function if exists public.allowed_search_kind_masks(text[]);
