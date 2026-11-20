-- PRD 4.3-A, 5 — Shared conversational search state, service_role only.
-- Additive; no change to existing item/search RPCs. Deploy SQL before the app.
create table public.agent_search_sessions (
    id text primary key,
    data jsonb not null,
    revision integer not null default 0,
    expires_at timestamptz not null,
    run_id text,
    lease_until timestamptz,
    cancelled boolean not null default false,
    request_id text,
    fingerprint text,
    response jsonb,
    seen jsonb not null default '[]'::jsonb
);
create index agent_search_sessions_expiry on public.agent_search_sessions (expires_at);
alter table public.agent_search_sessions enable row level security;
revoke all on public.agent_search_sessions from public, anon, authenticated;
grant all on public.agent_search_sessions to service_role;

create function public.agent_session_command(p_session_id text, p_command text, p_args jsonb default '{}'::jsonb)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
    r public.agent_search_sessions%rowtype;
    t timestamptz := clock_timestamp();
    result jsonb;
    same_request boolean;
begin
    -- Lazy cleanup also covers a delayed scheduled cleanup.
    delete from public.agent_search_sessions where expires_at <= t;
    if p_command = 'claim' and coalesce((p_args->>'create')::boolean, false) then
        -- Retain the existing capacity limit across all instances, not per process.
        perform pg_advisory_xact_lock(617204311);
        if not exists (select from public.agent_search_sessions where id = p_session_id)
           and (select count(*) from public.agent_search_sessions) >= 128 then
            return '{"outcome":"session_capacity"}'::jsonb;
        end if;
        insert into public.agent_search_sessions (id, data, expires_at)
        values (p_session_id, p_args->'initial', t + interval '30 minutes')
        on conflict (id) do nothing;
    end if;
    select * into r from public.agent_search_sessions where id = p_session_id for update;
    if not found then
        return jsonb_build_object('outcome', 'session_expired');
    end if;
    if p_command = 'get' then
        return jsonb_build_object('outcome', 'ok', 'data', r.data, 'revision', r.revision, 'run_id', r.run_id);
    elsif p_command = 'claim' then
        same_request := coalesce(r.request_id = p_args->>'request_id', false);
        if same_request and r.fingerprint is distinct from p_args->>'fingerprint' then
            return '{"outcome":"state_conflict"}'::jsonb;
        end if;
        if same_request and r.response is not null then
            update public.agent_search_sessions set expires_at = t + interval '30 minutes' where id = p_session_id;
            return jsonb_build_object('outcome', 'replay', 'response', r.response);
        end if;
        if r.data->>'status' = 'ended' then
            return '{"outcome":"state_conflict"}'::jsonb;
        end if;
        if r.run_id is not null and r.lease_until > t then
            return '{"outcome":"busy"}'::jsonb;
        end if;
        if r.revision <> (p_args->>'revision')::integer
           or (not same_request and r.seen ? (p_args->>'request_id')) then
            return '{"outcome":"state_conflict"}'::jsonb;
        end if;
        if not same_request then
            if jsonb_array_length(r.seen) >= 100 then
                return '{"outcome":"session_expired"}'::jsonb;
            end if;
            r.seen := r.seen || jsonb_build_array(p_args->>'request_id');
        end if;
        update public.agent_search_sessions set
            run_id = p_args->>'run_id', request_id = p_args->>'request_id', fingerprint = p_args->>'fingerprint',
            response = null, cancelled = false, lease_until = t + interval '120 seconds',
            expires_at = t + interval '30 minutes', seen = r.seen
        where id = p_session_id;
        return jsonb_build_object('outcome', 'claimed', 'data', r.data);
    elsif p_command = 'cancel' then
        if r.run_id = p_args->>'run_id' and r.lease_until > t then
            update public.agent_search_sessions set cancelled = true where id = p_session_id;
            return '{"outcome":"ok","accepted":true}'::jsonb;
        end if;
        return '{"outcome":"ok","accepted":false}'::jsonb;
    elsif p_command = 'check' then
        return jsonb_build_object('outcome', 'ok', 'cancelled',
            r.run_id is distinct from p_args->>'run_id' or r.cancelled or coalesce(r.lease_until <= t, true));
    elsif p_command = 'finish' then
        if r.run_id is distinct from p_args->>'run_id' or r.lease_until <= t then
            return '{"outcome":"state_conflict"}'::jsonb;
        end if;
        result := p_args->'response';
        if r.cancelled or not (p_args->>'commit')::boolean then
            if r.cancelled then
                result := result || '{"status":"cancelled","message":"검색을 취소했어요.","error":null}'::jsonb;
            end if;
            result := result || (p_args->'previous') || jsonb_build_object('revision', r.revision,
                'matches', null, 'recommended_item_ids', '[]'::jsonb, 'search_attempts', '[]'::jsonb);
        else
            r.data := p_args->'data';
            r.revision := (result->>'revision')::integer;
        end if;
        update public.agent_search_sessions set data = r.data, revision = r.revision, run_id = null,
            expires_at = t + interval '30 minutes',
            response = case when result->>'status' = 'error' then null else result end
        where id = p_session_id;
        return jsonb_build_object('outcome', 'ok', 'response', result);
    elsif p_command = 'page' then
        if r.run_id is not null or r.revision <> (p_args->>'revision')::integer
           or r.data->>'cursor' is distinct from p_args->>'cursor' then
            return '{"outcome":"state_conflict"}'::jsonb;
        end if;
        update public.agent_search_sessions set
            data = jsonb_set(jsonb_set(data, '{cursor}', p_args->'next_cursor'), '{items}',
                coalesce(data->'items', '{}'::jsonb) || coalesce(p_args->'items', '{}'::jsonb)), expires_at = t + interval '30 minutes'
        where id = p_session_id;
        update public.agent_search_sessions set data = jsonb_set(data, '{items}',
            (select coalesce(jsonb_object_agg(key, value), '{}'::jsonb) from
                (select key, value from jsonb_each(data->'items') limit 100) cache)) where id = p_session_id;
        return '{"outcome":"ok"}'::jsonb;
    end if;
    raise exception 'Unknown session command';
end;
$$;
revoke all on function public.agent_session_command(text, text, jsonb) from public, anon, authenticated;
grant execute on function public.agent_session_command(text, text, jsonb) to service_role;

-- Delete idle payloads even with no subsequent traffic. Logical expiry is exact;
-- scheduled physical deletion occurs at the next minute (or after scheduler recovery).
create extension if not exists pg_cron with schema pg_catalog;
-- Supabase Cron installation privileges; no browser role receives access.
grant usage on schema cron to postgres;
grant all privileges on all tables in schema cron to postgres;
select cron.schedule('agent-search-session-expiry', '* * * * *',
    $job$delete from public.agent_search_sessions where expires_at <= clock_timestamp();
    delete from cron.job_run_details where jobid = (select jobid from cron.job where jobname = 'agent-search-session-expiry')
        and end_time < clock_timestamp() - interval '1 day'$job$);
