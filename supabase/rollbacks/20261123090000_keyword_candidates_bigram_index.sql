-- 20261123090000_keyword_candidates_bigram_index 되돌리기(수동 실행).
-- 함수를 20261121120000의 본문으로 되돌린 뒤 인덱스와 조각 함수를 지운다. 순서를 바꾸면 함수가 없는 조각 함수를 부른다.

create or replace function public.keyword_found_item_candidates(p_location_ids bigint[],p_terms text[],p_word_terms text[] default '{}')
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

drop index if exists public.found_item_search_documents_bigrams;
drop function if exists public.search_bigrams(text);
notify pgrst, 'reload schema';
