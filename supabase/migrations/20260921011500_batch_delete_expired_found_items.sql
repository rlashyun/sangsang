-- PRD 4.5 · PRD 5(신뢰성) — 보존기간 삭제를 배치로 쪼갠다.
--
-- 기존 delete_expired_found_items는 만료분 전량을 단일 DELETE로 지웠다.
-- found_items가 약 30만 행으로 커지면서 Postgres statement_timeout에 걸려
-- 57014(canceling statement due to statement timeout)로 취소되고, 크론이
-- 매일 ingestion_runs.status = 'partial' / deleted_count = 0 으로 끝났다.
--
-- p_limit 건씩 끊어 지우고, 호출 측(store.delete_expired)이 짧은 배치가
--나올 때까지 반복한다. 남은 만료분은 다음 실행이 이어서 지운다 — 멱등이므로
-- 중간에 멈춰도 안전하다(PRD 3.1-1).
--
-- 옛 단일 인자 시그니처를 남기면 PostgREST가 오버로드로 보고 호출이 모호해지므로
-- 반드시 먼저 제거한다.

drop function if exists public.delete_expired_found_items(text);

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
        select s.code, s.retention_days
        from public.item_sources as s
        where s.enabled
          and (p_source_code is null or s.code = p_source_code)
    ),
    expiring as materialized (
        -- 작업량을 묶는 것은 LIMIT이다. retention_days가 조인된 item_sources에서
        -- 오므로 조건이 상수가 아니고, 계획은 인덱스가 아닌 seq scan + LIMIT이
        -- 된다. 조기 종료는 그대로 일어난다.
        -- PG16 실측: 30만 행·대부분 만료 1.8ms / 4만 행·만료 소수 16.7ms.
        select fi.id
        from public.found_items as fi
        join target_sources as ts on fi.item_source_code = ts.code
        where fi.registered_on < current_date - (ts.retention_days::integer - 1)
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
    'Deletes up to p_limit found items outside each enabled source retention window. Call repeatedly until a short batch. Call only after a successful source sync.';

-- 절대규칙 6 — service_role 전용. drop이 기존 권한을 지우므로 다시 부여한다.
revoke all on function public.delete_expired_found_items(text, integer)
    from public, anon, authenticated;
grant execute on function public.delete_expired_found_items(text, integer)
    to service_role;
