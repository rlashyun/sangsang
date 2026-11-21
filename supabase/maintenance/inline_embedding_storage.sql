-- PRD 5 — 2026-11-21 김성현 지시: 원본 벡터(found_item_embeddings_bge_m3.embedding)를 TOAST에서 행 안으로 옮긴다.
-- 전제: 마이그레이션 20261121190000_one_bit_semantic_search 적용 뒤(사본 1.75GB 삭제, storage plain 지정).
-- 이유: 재채점 500건이 벡터 1건마다 TOAST 색인·청크를 더 읽는다(캐시 없을 때 약 0.2~0.3초 차이).
-- 방식: 한 번에 하나씩, item_id 구간 단위 UPDATE. VACUUM FULL은 표 전체 잠금과 WAL 한 번 폭증이라 쓰지 않는다.
--   - 같은 값을 다시 넣으면 TOAST 포인터가 그대로 남으므로 vector로 바꿨다가 halfvec으로 돌린다(값 손실 없음).
--   - pg_column_toast_chunk_id(PG17)가 NULL이 아닌 행만 고치므로 중간에 멈춰도 다시 실행하면 이어서 한다.
--   - UPDATE마다 embeddings_sync_search 트리거가 돌지만 1비트 값이 같으면 쓰지 않는다(refresh_search_vector).
-- 예상: 61만 행 → 힙 약 1.6GB로 늘고, 끝난 뒤 VACUUM이 비어 있는 TOAST를 잘라 약 1.6GB를 돌려준다.

-- 0) 시작 전·구간마다 확인: 수집·임베딩 실행 중이 아니고, WAL이 3GB 아래이고, 아카이브 실패가 없을 것.
select (select count(*) from public.ingestion_runs where status = 'running') ingestion_running,
       (select count(*) from public.embedding_runs where status = 'running') embedding_running,
       (select pg_size_pretty(sum(size)) from pg_ls_waldir()) wal,
       (select last_failed_wal from pg_stat_archiver) last_failed_wal,
       pg_size_pretty(pg_database_size(current_database())) db;

-- 1) 구간 하나(:lo < item_id <= :hi, 약 4만 id ≈ 3만 행)를 옮긴다. 한 번에 하나씩 실행한다.
set statement_timeout = '120s';
update public.found_item_embeddings_bge_m3
set embedding = (embedding::extensions.vector(1024))::extensions.halfvec(1024)
where item_id > :lo and item_id <= :hi
  and pg_column_toast_chunk_id(embedding) is not null;

-- 2) 몇 구간마다(또는 WAL이 커지면) 죽은 행을 정리해 힙 공간을 다시 쓰게 한다.
-- vacuum public.found_item_embeddings_bge_m3;

-- 3) 남은 TOAST 행 확인 → 0이면 마지막 VACUUM으로 빈 TOAST를 잘라 낸다.
select count(*) filter (where pg_column_toast_chunk_id(embedding) is not null) still_toasted, count(*) total
from public.found_item_embeddings_bge_m3;
-- vacuum (verbose) public.found_item_embeddings_bge_m3;
select pg_size_pretty(pg_relation_size('public.found_item_embeddings_bge_m3')) heap,
       pg_size_pretty(pg_relation_size(reltoastrelid)) toast
from pg_class where oid = 'public.found_item_embeddings_bge_m3'::regclass;
