-- PRD 4.5, 5 — 키워드 후보 포함 검사에 2글자 조각(bigram) GIN 인덱스를 붙인다.
--
-- 문제(2026-11-22 운영, docs/measurements.md): 포함 검사(strpos)에 쓸 인덱스가 없어 반경 안 문서를 전부 읽었다.
-- 서울역 1km '지갑'은 30,626행(2,785페이지)을 열어 3,294행을 남긴다. 캐시가 데워졌을 땐 0.22초지만
-- found_item_search_documents(508MB)가 shared_buffers(256MB)에서 밀려나면 1.8초 제한(57014)에 걸려 검색이 502가 됐다.
--
-- 방법: 공백을 모두 지운 search_text의 서로 다른 2글자 조각 배열을 GIN으로 색인한다.
-- 후보어가 search_text에 들어 있으면(strpos > 0) 공백을 지운 후보어의 조각은 모두 공백을 지운 문서의 조각에 들어 있다.
-- 그래서 조각 조건(@>)은 strpos 조건의 필요조건이고, 기존 strpos 검사를 그대로 다시 하므로 결과 행은 바뀌지 않는다.
-- BM25가 점수를 줄 물품을 먼저 버리지 않는다는 원칙(PRD 4.5, 2026-11-21)이 그대로 지켜진다.
--
-- 인덱스를 쓰지 않는 경우(기존과 같은 전체 확인): 1글자 독립 단어(p_word_terms)가 있거나,
-- 공백을 빼면 1글자 이하인 후보어(옷·책 등)가 하나라도 있을 때. 이때는 이전 함수와 같은 쿼리를 실행한다.
--
-- 적용: 인덱스 생성이 found_item_search_documents 쓰기를 잠시 막는다(읽기는 된다). 수집 크론(09시·21시대)과
-- 임베딩 복구(11시·23시대)를 피해 적용한다. 함수 시그니처·응답 형식은 같아 앱 배포 순서와 무관하다.
-- 되돌리기: supabase/rollbacks/20261123090000_keyword_candidates_bigram_index.sql을 수동 실행한다.

set local statement_timeout = '15min';
set local maintenance_work_mem = '128MB';

-- offset 0: 공백 제거를 행마다 한 번만 한다. 없으면 플래너가 하위 쿼리를 펼쳐 조각마다(행당 약 50번) 다시 계산한다.
-- 운영 5% 표본(약 3.1만 행) 측정: 펼친 형태 17.8초(행당 588µs) → offset 0 4.6초(행당 147µs). docs/measurements.md.
create function public.search_bigrams(p_text text)
returns text[] language sql immutable strict parallel safe set search_path = '' as $$
  select coalesce(array_agg(distinct substr(s.t, i, 2)), '{}'::text[])
  from (select regexp_replace(p_text, '[[:space:]]+', '', 'g') as t offset 0) s
  cross join lateral generate_series(1, char_length(s.t) - 1) i
$$;
revoke all on function public.search_bigrams(text) from public, anon, authenticated;
grant execute on function public.search_bigrams(text) to service_role;

create index found_item_search_documents_bigrams
  on public.found_item_search_documents using gin (public.search_bigrams(search_text));

create or replace function public.keyword_found_item_candidates(p_location_ids bigint[],p_terms text[],p_word_terms text[] default '{}')
returns json language plpgsql stable security invoker set search_path='' set jit='off' set statement_timeout='1800ms' as $$
declare
 result json;
 gram_filter text;
begin
 -- 인덱스로 좁힐 수 있을 때만 조각 조건을 더한다. 조각 배열은 서버가 아니라 여기서 같은 함수로 만든다.
 if cardinality(coalesce(p_word_terms,'{}'::text[]))=0
    and exists(select 1 from unnest(p_terms) t(term) where t.term<>'')
    and not exists(select 1 from unnest(p_terms) t(term)
                   where t.term<>'' and cardinality(public.search_bigrams(lower(t.term)))=0) then
  select string_agg('public.search_bigrams(d.search_text) @> '||quote_literal(public.search_bigrams(lower(t.term)))||'::text[]',' or ')
    into gram_filter
  from (select distinct term from unnest(p_terms) u(term) where u.term<>'') t;
 end if;
 execute $query$
  select json_build_object('candidate_count',null,'items',coalesce(json_agg(r),'[]'::json))
  from (
   select d.item_id as id,d.storage_location_id,d.search_text,x.item_name,x.raw_category_name
   from public.found_item_search_documents d
   cross join lateral jsonb_to_record(d.detail) x(item_name text,raw_category_name text)
   where d.storage_location_id=any($1) and cardinality($1) between 1 and 300
  $query$ || coalesce(' and ('||gram_filter||')','') || $query$
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
