-- PRD 4.5 — date-scoped overloads preserve existing date-less RPC calls during rollout.
create function public.found_item_ids_registered_since(p_location_ids bigint[], p_lost_on date)
returns table(item_id bigint)
language sql stable security invoker set search_path = ''
as $$
  select f.id
  from public.found_items f
  where f.storage_location_id is not null
    and f.storage_location_id = any(p_location_ids)
    and f.registered_on >= p_lost_on;
$$;

create function public.keyword_found_item_candidates(
  p_location_ids bigint[], p_terms text[], p_word_terms text[], p_lost_on date
)
returns json language plpgsql stable security invoker
set search_path = '' set jit = 'off' set statement_timeout = '1800ms'
as $$
declare
  result json;
  gram_filter text;
begin
  if cardinality(coalesce(p_word_terms, '{}'::text[])) = 0
     and exists (select 1 from unnest(p_terms) t(term) where t.term <> '')
     and not exists (
       select 1 from unnest(p_terms) t(term)
       where t.term <> '' and cardinality(public.search_bigrams(lower(t.term))) = 0
     ) then
    select string_agg(
      'public.search_bigrams(d.search_text) @> ' ||
      quote_literal(public.search_bigrams(lower(t.term))) || '::text[]', ' or '
    ) into gram_filter
    from (select distinct term from unnest(p_terms) u(term) where term <> '') t;
  end if;
  execute $query$
    select json_build_object('candidate_count', null, 'items', coalesce(json_agg(r), '[]'::json))
    from (
      select d.item_id as id, d.storage_location_id, d.search_text, x.item_name, x.raw_category_name
      from public.found_item_search_documents d
      join public.found_item_ids_registered_since($1, $4) eligible on eligible.item_id = d.item_id
      cross join lateral jsonb_to_record(d.detail) x(item_name text, raw_category_name text)
      where d.storage_location_id = any($1) and cardinality($1) between 1 and 300
  $query$ || coalesce(' and (' || gram_filter || ')', '') || $query$
        and (exists (select 1 from unnest($2) t(term) where term <> '' and strpos(d.search_text, lower(t.term)) > 0)
         or exists (select 1 from unnest($3) w(word) where w.word <> '' and strpos(d.search_text, lower(w.word)) > 0
           and d.search_text ~ ('(^|[^[:alnum:]])' || regexp_replace(lower(w.word), '([^[:alnum:]])', '\\\1', 'g') || '($|[^[:alnum:]])')))
    ) r
  $query$ into result using p_location_ids, p_terms, coalesce(p_word_terms, '{}'::text[]), p_lost_on;
  return result;
end;
$$;

create function public.semantic_found_item_candidates_gemini2(
  p_location_ids bigint[], p_query extensions.halfvec, p_min float8,
  p_limit int, p_rescore int, p_lost_on date
)
returns table(item_id bigint, similarity float8, detail jsonb)
language plpgsql stable security invoker
set search_path = '' set jit = 'off' set statement_timeout = '2500ms'
as $$
declare q bit(1024);
begin
  if cardinality(p_location_ids) is null or cardinality(p_location_ids) not between 1 and 300
     or p_limit not between 1 and 200 or p_rescore not between p_limit and 2000
     or extensions.vector_dims(p_query) <> 1024 then
    raise exception 'invalid semantic search arguments';
  end if;
  q := extensions.binary_quantize(p_query)::bit(1024);
  return query
  with c as materialized (
    select b.item_id
    from public.found_item_search_vectors_gemini2_1bit b
    join public.found_item_ids_registered_since(p_location_ids, p_lost_on) eligible
      on eligible.item_id = b.item_id
    where b.storage_location_id = any(p_location_ids)
    order by bit_count(b.embedding # q), b.item_id desc limit p_rescore
  ), s as materialized (
    select e.item_id, (1 - (e.embedding operator(extensions.<=>) p_query))::float8 sim
    from c join public.found_item_embeddings_gemini2 e on e.item_id = c.item_id
  ), top as materialized (
    select s.item_id, s.sim from s where s.sim >= p_min order by s.sim desc, s.item_id desc limit p_limit
  )
  select t.item_id, t.sim, d.detail from top t
  join public.found_item_search_documents d on d.item_id = t.item_id and d.storage_location_id = any(p_location_ids)
  order by t.sim desc, t.item_id desc;
end;
$$;

revoke all on function public.found_item_ids_registered_since(bigint[], date),
  public.keyword_found_item_candidates(bigint[], text[], text[], date),
  public.semantic_found_item_candidates_gemini2(bigint[], extensions.halfvec, float8, int, int, date)
  from public, anon, authenticated;
grant execute on function public.found_item_ids_registered_since(bigint[], date),
  public.keyword_found_item_candidates(bigint[], text[], text[], date),
  public.semantic_found_item_candidates_gemini2(bigint[], extensions.halfvec, float8, int, int, date)
  to service_role;
notify pgrst, 'reload schema';
