-- PRD 4.5 / 5 — 2026-11-21 김성현 지시: 원본 임베딩 행을 TOAST 없이 통째로 행 안에 둔다.
-- 배경(docs/measurements.md): 20261121190000에서 벡터 열을 storage plain으로 바꾸고 기존 행을 옮기자 행이 약 2,258바이트가 됐다.
--   기본 TOAST 기준(약 2KB)을 넘어서 content_hash(전 행)·embedded_input(약 절반) 같은 짧은 글자가 대신 TOAST로 밀려났고,
--   그 작은 값들이 TOAST 파일(1.49GB) 곳곳에 남아 VACUUM이 파일을 잘라 내지 못한다.
-- 1) toast_tuple_target을 4000바이트로 올린다. 4000 이하 행은 압축·TOAST 이동을 하지 않는다(카탈로그만 바뀌어 즉시 끝난다).
--    운영 임시 표에서 확인: 기본값 50행 중 content_hash 50·embedded_input 27개가 TOAST로 갔고, 4000에서는 0개.
--    이미 밀려난 값은 supabase/maintenance/inline_embedding_storage.sql 2단계로 나눠 행 안으로 되돌린 뒤 VACUUM한다.
-- 2) keyword_rerank_vectors 삭제: 20261121190000 이후 앱이 부르지 않는다(배포 전환 호환용으로만 남겨 뒀다).
-- 되돌리기: supabase/rollbacks/ 아래 같은 버전·이름의 SQL을 수동 실행한다.
set lock_timeout = '5s';

alter table public.found_item_embeddings_bge_m3 set (toast_tuple_target = 4000);

drop function if exists public.keyword_rerank_vectors(bigint[], bigint[]);

notify pgrst, 'reload schema';
reset lock_timeout;
