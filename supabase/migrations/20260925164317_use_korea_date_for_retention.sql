-- PRD 3.1-2, 4.1 — DB 서버는 UTC이므로 한국 날짜로 6개월 시작일을 계산한다.
create or replace function public.delete_expired_found_items(
    p_source_code text default null,
    p_limit integer default 5000
)
returns table (source_code text, deleted_count bigint)
language sql
security invoker
set search_path = ''
as $$
    with target_sources as materialized (
        select s.code, s.retention_months
        from public.item_sources as s
        where s.enabled
          and (p_source_code is null or s.code = p_source_code)
    ),
    expiring as materialized (
        select fi.id
        from public.found_items as fi
        join target_sources as ts on fi.item_source_code = ts.code
        where fi.registered_on < (
            (now() at time zone 'Asia/Seoul')::date
            - make_interval(months => ts.retention_months::integer)
        )::date
        limit greatest(coalesce(p_limit, 5000), 0)
    ),
    deleted as (
        delete from public.found_items as fi
        using expiring as e
        where fi.id = e.id
        returning fi.item_source_code
    )
    select ts.code, count(d.item_source_code)::bigint
    from target_sources as ts
    left join deleted as d on d.item_source_code = ts.code
    group by ts.code
    order by ts.code;
$$;

comment on function public.delete_expired_found_items(text, integer) is
    'Deletes up to p_limit rows before the Korean-date six-calendar-month window; call only after a successful source sync.';

revoke all on function public.delete_expired_found_items(text, integer)
    from public, anon, authenticated;
grant execute on function public.delete_expired_found_items(text, integer)
    to service_role;
