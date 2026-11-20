-- 수동 복구 자료 — 자동 실행하거나 migrations/로 옮기지 않는다(supabase/README.md).
-- 20261121120000_keyword_candidates_word_terms.sql을 되돌린다.
-- 이전 2인자 정의(20261120120000_keyword_candidates_without_kind_mask.sql, 2026-11-21 운영 정의와 동일)를 복원한다.
-- 되돌린 뒤에는 p_word_terms를 보내는 앱의 1글자 검색이 함수를 찾지 못한다(PGRST202). 앱도 함께 되돌린다.

drop function if exists public.keyword_found_item_candidates(bigint[],text[],text[]);
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
