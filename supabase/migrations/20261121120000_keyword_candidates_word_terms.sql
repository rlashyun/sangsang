-- PRD 4.5 — 키워드 후보 포함 검사에 '독립 단어로만 비교하는 1글자 단어'(p_word_terms)를 더한다.
-- 서버 BM25는 1글자 검색어(키·펜·책)를 조각 없이 독립 단어로만 점수 매긴다. 그런데 strpos는 나이키·카드키·키링까지
-- 통과시켜 0점이 될 물품을 서버로 보냈다(2026-11-21 서울역 1km '키' 732건 중 독립 단어 11건, docs/measurements.md).
-- 서버는 사전 단어·대표어가 아닌 1글자 단어만 p_word_terms로 보낸다. 앞뒤가 글자·숫자가 아닐 때만 통과하므로
-- BM25가 점수를 줄 물품은 그대로 남는다. 나머지 p_terms의 strpos 조건과 범위 필터는 바꾸지 않는다.
--
-- 적용 순서: 이 마이그레이션 → 앱 배포. p_word_terms 기본값이 빈 배열이라 이전 앱(2인자 호출)도 그대로 동작한다.
-- 이전 2인자 함수는 지운다. 남겨 두면 2인자 호출이 두 함수 중 어느 쪽인지 모호해진다(PostgREST PGRST203).
-- 새 앱은 1글자 단어가 있을 때만 p_word_terms를 보내므로, 이 마이그레이션 전이면 그 검색만 실패한다.
-- 되돌리기: supabase/rollbacks/20261121120000_keyword_candidates_word_terms.sql을 수동 실행한다.

drop function public.keyword_found_item_candidates(bigint[],text[]);
create function public.keyword_found_item_candidates(p_location_ids bigint[],p_terms text[],p_word_terms text[] default '{}')
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
    and (exists(select 1 from unnest($2) t(term) where term<>'' and strpos(d.search_text,lower(t.term))>0)
     or exists(select 1 from unnest($3) w(word) where w.word<>'' and strpos(d.search_text,lower(w.word))>0
      and d.search_text ~ ('(^|[^[:alnum:]])'||regexp_replace(lower(w.word),'([^[:alnum:]])','\\\1','g')||'($|[^[:alnum:]])')))
  ) r
 $query$ into result using p_location_ids,p_terms,coalesce(p_word_terms,'{}'::text[]);
 return result;
end $$;
revoke all on function public.keyword_found_item_candidates(bigint[],text[],text[]) from public,anon,authenticated;
grant execute on function public.keyword_found_item_candidates(bigint[],text[],text[]) to service_role;
notify pgrst, 'reload schema';
