-- PRD 4.1, 4.5 — 키워드 후보 RPC에서 DB 종류 비트(kind_mask) 필터와 p_kinds 인자를 뺀다.
-- 앱은 2026-11-14(85d31b8)부터 p_kinds를 항상 null로 보내 이 필터가 마스크 0~63 전부를 허용했다.
-- 종류 판단은 서버가 synonyms.json의 종류 표현(종류별 9~28개)을 p_terms로 넘겨 처리한다.
-- DB 마스크(search_kind_mask)는 종류별 2~6개 단어만 알아 별칭 물품을 빠뜨리므로 다시 켜지 않는다.
-- 결과 행은 바뀌지 않는다: p_kinds=null일 때와 같은 조건(기관 범위 + 단어 하나 이상 포함)이다.
--
-- 적용 순서: p_kinds를 보내지 않는 앱 배포 → 이 마이그레이션.
-- 새 앱은 이전 3인자 함수(p_kinds 기본값 null)도 그대로 호출한다. 반대로 이 마이그레이션을 먼저 적용하면
-- p_kinds를 보내는 이전 앱이 함수를 찾지 못해(PGRST202) 키워드 검색이 실패한다.
--
-- 그대로 두는 것: kind_mask 열·found_item_search_documents_kind 인덱스, search_kind_mask·
-- allowed_search_kind_masks 함수, 앱이 호출하지 않는 search_found_item_candidates·
-- search_found_items_semantic_fast(둘 다 아직 kind_mask를 참조한다).
-- 되돌리기: supabase/rollbacks/20261120120000_keyword_candidates_without_kind_mask.sql을 수동 실행한다.

drop function public.keyword_found_item_candidates(bigint[],text[],text[]);
create function public.keyword_found_item_candidates(p_location_ids bigint[],p_terms text[])
returns json language plpgsql stable security invoker set search_path='' set jit='off' set statement_timeout='1800ms' as $$
declare result json;
begin
 execute $query$
  select json_build_object('candidate_count',null,'items',coalesce(json_agg(r),'[]'::json))
  from (
   select d.item_id as id,d.storage_location_id,d.search_text,x.item_name,x.raw_category_name
   from public.found_item_search_documents d
   cross join lateral jsonb_to_record(d.detail) x(item_name text,raw_category_name text)
   where d.storage_location_id=any($1) and cardinality($1) between 1 and 300
    and exists(select 1 from unnest($2) t(term) where term<>'' and strpos(d.search_text,lower(t.term))>0)
  ) r
 $query$ into result using p_location_ids,p_terms;
 return result;
end $$;
revoke all on function public.keyword_found_item_candidates(bigint[],text[]) from public,anon,authenticated;
grant execute on function public.keyword_found_item_candidates(bigint[],text[]) to service_role;
notify pgrst, 'reload schema';
