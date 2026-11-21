-- PRD 3.3 · Q5 — 마스킹 규칙 전에 원문으로 만든 물품 벡터를 가린 입력으로 만든 벡터로 바꾼다.
--
-- 기존 저장 RPC(upsert_bge_m3_embeddings)는 on conflict do nothing이라 이미 있는 벡터를 바꾸지 못한다.
-- 이 함수는 수동 도구 `python -m retriever_lost_found.privacy.reembed --apply`만 부른다(service_role 전용).
--
-- 지키는 것
-- - 벡터와 embedded_at만 바꾼다. embedded_input·content_hash(원문 입력의 해시 검증용)는 그대로다.
-- - 지금 물품 입력과 저장 스냅숏이 모두 도구가 읽은 값과 같을 때만 바꾼다(그 사이 물품이 바뀌었으면 건너뜀).
-- - p_embedded_before보다 먼저 만든 벡터만 바꾼다. 바꾼 행은 embedded_at=now()가 되어 다시 대상이 되지 않는다.
--   재사용 함수(reuse_run_embeddings)는 원본 벡터의 embedded_at을 그대로 복사하므로, 원문 벡터를 복사받은
--   행도 같은 기준으로 잡힌다.
-- - 검색 벡터 표는 기존 트리거 embeddings_sync_search가 refresh_search_vector로 갱신한다.

create function public.replace_masked_bge_m3_embeddings(records jsonb, p_embedded_before timestamptz)
returns bigint language plpgsql security invoker set search_path='' set statement_timeout='30s' as $$
declare replaced bigint;
begin
 if p_embedded_before is null then raise exception 'embedded_before is required'; end if;
 if jsonb_array_length(records)>32 then raise exception 'embedding batch too large'; end if;
 with supplied as (
  select * from jsonb_to_recordset(records)
   as r(item_id bigint,content_hash text,embedding text,embedded_input text)
 ), updated as (
  update public.found_item_embeddings_bge_m3 e
  set embedding=s.embedding::extensions.halfvec(1024), embedded_at=now()
  from supplied s, public.found_items f
  where e.item_id=s.item_id and f.id=s.item_id
   and e.is_current and e.input_version='color_ko_item_name:v2'
   and e.embedded_at<p_embedded_before
   and e.embedded_input=s.embedded_input and e.content_hash=s.content_hash
   and f.embedding_input=s.embedded_input
  returning e.item_id
 ) select count(*) into replaced from updated;
 return replaced;
end $$;
revoke all on function public.replace_masked_bge_m3_embeddings(jsonb,timestamptz) from public,anon,authenticated;
grant execute on function public.replace_masked_bge_m3_embeddings(jsonb,timestamptz) to service_role;
