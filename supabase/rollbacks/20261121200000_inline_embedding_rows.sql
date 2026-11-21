-- 되돌리기: inline_embedding_rows. 수동 실행한다.
-- toast_tuple_target을 기본값으로 돌린다. 이미 행 안에 든 값은 다음에 그 행이 바뀔 때 다시 TOAST로 갈 수 있다(검색에는 영향 없음).
set lock_timeout = '5s';

alter table public.found_item_embeddings_bge_m3 reset (toast_tuple_target);

-- 앱은 이 함수를 부르지 않는다. 이전 앱으로 되돌릴 때만 다시 만든다(20261121190000과 같은 정의).
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
revoke all on function public.keyword_rerank_vectors(bigint[], bigint[]) from public, anon, authenticated;
grant execute on function public.keyword_rerank_vectors(bigint[], bigint[]) to service_role;

-- delete from supabase_migrations.schema_migrations where version = '20261121200000';
notify pgrst, 'reload schema';
