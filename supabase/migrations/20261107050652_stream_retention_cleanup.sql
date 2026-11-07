-- PRD 4.5, 5 — stream the bounded expiry range through the existing source/date index.
-- Version aligned with the migration generated on production.
-- The old join could build a bitmap for the whole expired range before honoring LIMIT.
-- Preserve source scoping, KST calendar-month retention, total batch cap and service-only access.
create or replace function public.delete_expired_found_items(
    p_source_code text default null, p_limit integer default 5000)
returns table(source_code text,deleted_count bigint)
language sql security invoker set search_path='' as $$
    with target_sources as materialized (
        select s.code,s.retention_months from public.item_sources s
        where s.enabled and (p_source_code is null or s.code=p_source_code)
    ), expiring as materialized (
        select candidate.id from target_sources ts
        cross join lateral (
            select fi.id from public.found_items fi
            where fi.item_source_code=ts.code
              and fi.registered_on < ((now() at time zone 'Asia/Seoul')::date
                  - make_interval(months=>ts.retention_months::integer))::date
            order by fi.registered_on
            limit greatest(coalesce(p_limit,5000),0)
        ) candidate
        limit greatest(coalesce(p_limit,5000),0)
    ), deleted as (
        delete from public.found_items fi using expiring e
        where fi.id=e.id returning fi.item_source_code
    )
    select ts.code,count(d.item_source_code)::bigint
    from target_sources ts left join deleted d on d.item_source_code=ts.code
    group by ts.code order by ts.code;
$$;
revoke all on function public.delete_expired_found_items(text,integer) from public,anon,authenticated;
grant execute on function public.delete_expired_found_items(text,integer) to service_role;
notify pgrst,'reload schema';
