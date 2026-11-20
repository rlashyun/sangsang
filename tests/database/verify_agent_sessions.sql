-- Run against an isolated DB after 20261120150000_agent_search_sessions.sql.
-- Rolls back every synthetic row. Does not call external services.
begin;
set local role service_role;
do $$
declare
    args jsonb := '{"create":true,"initial":{"revision":0,"status":"","cursor":"c","items":{}},"request_id":"first","fingerprint":"f","revision":0,"run_id":"a"}';
    reply jsonb;
    completion jsonb := '{"run_id":"a","commit":true,"data":{"revision":1,"status":"results","cursor":"next","items":{}},"response":{"status":"results","revision":1},"previous":{"conditions":{"item_query":null}}}';
begin
    reply := public.agent_session_command('verify-session', 'claim', args);
    assert reply->>'outcome' = 'claimed', 'initial claim';
    assert public.agent_session_command('verify-session', 'claim', args)->>'outcome' = 'busy', 'duplicate cannot execute';
    assert public.agent_session_command('verify-session', 'claim', args || '{"fingerprint":"changed"}')->>'outcome' = 'state_conflict', 'payload binding';
    assert public.agent_session_command('verify-session', 'cancel', '{"run_id":"wrong"}')->>'accepted' = 'false', 'wrong run';
    assert public.agent_session_command('verify-session', 'check', '{"run_id":"a"}')->>'cancelled' = 'false', 'active run';
    assert public.agent_session_command('verify-session', 'cancel', '{"run_id":"a"}')->>'accepted' = 'true', 'cancel';
    assert public.agent_session_command('verify-session', 'check', '{"run_id":"a"}')->>'cancelled' = 'true', 'shared cancellation';
    reply := public.agent_session_command('verify-session', 'finish', completion);
    assert reply->'response'->>'status' = 'cancelled', 'cancel wins finish';
    assert reply->'response'->>'revision' = '0', 'cancel keeps revision';
    assert public.agent_session_command('verify-session', 'claim', args)->>'outcome' = 'replay', 'cancel replays terminal outcome';
    args := args || '{"request_id":"retry-after-cancel"}';
    assert public.agent_session_command('verify-session', 'claim', args)->>'outcome' = 'claimed', 'new request after cancel';
    reply := public.agent_session_command('verify-session', 'finish', completion || '{"commit":false,"response":{"status":"error","revision":0}}');
    assert reply->'response'->>'revision' = '0', 'failed turn keeps revision';
    assert public.agent_session_command('verify-session', 'claim', args)->>'outcome' = 'claimed', 'failed turn retry';
    assert public.agent_session_command('verify-session', 'finish', completion)->'response'->>'status' = 'results', 'success';
    reply := public.agent_session_command('verify-session', 'claim', args);
    assert reply->>'outcome' = 'replay' and reply->'response'->>'revision' = '1', 'lost response replay';
    assert public.agent_session_command('verify-session', 'cancel', '{"run_id":"a"}')->>'accepted' = 'false', 'finish wins cancel';
    assert public.agent_session_command('verify-session', 'claim', args || '{"request_id":"new"}')->>'outcome' = 'state_conflict', 'stale revision';
    assert public.agent_session_command('verify-session', 'page', '{"revision":1,"cursor":"wrong","next_cursor":null,"items":{}}')->>'outcome' = 'state_conflict', 'invalid cursor';
    assert public.agent_session_command('verify-session', 'page', '{"revision":1,"cursor":"next","next_cursor":null,"items":{"1":{"item_id":1}}}')->>'outcome' = 'ok', 'page update';
    assert public.agent_session_command('verify-session', 'get')->'data'->'cursor' = 'null'::jsonb, 'cursor persisted';
    assert public.agent_session_command('verify-session', 'get')->'data'->'items' ? '1', 'detail cache persisted';
    assert public.agent_session_command('verify-session', 'claim', args || '{"request_id":"second","revision":1,"run_id":"old"}')->>'outcome' = 'claimed', 'new worker';
    update public.agent_search_sessions set lease_until = clock_timestamp() - interval '1 second' where id = 'verify-session';
    assert public.agent_session_command('verify-session', 'check', '{"run_id":"old"}')->>'cancelled' = 'true', 'expired lease';
    assert public.agent_session_command('verify-session', 'claim', args || '{"request_id":"second","revision":1,"run_id":"new"}')->>'outcome' = 'claimed', 'recover dead worker';
    assert public.agent_session_command('verify-session', 'finish', completion || '{"run_id":"old"}')->>'outcome' = 'state_conflict', 'fence old worker';
    update public.agent_search_sessions set expires_at = clock_timestamp() - interval '1 second' where id = 'verify-session';
    assert public.agent_session_command('verify-session', 'get')->>'outcome' = 'session_expired', 'logical expiry';
    assert not exists (select from public.agent_search_sessions where id = 'verify-session'), 'physical cleanup';
end $$;
reset role;
do $$
begin
    assert not has_table_privilege('anon', 'public.agent_search_sessions', 'SELECT'), 'anon blocked';
    assert not has_table_privilege('authenticated', 'public.agent_search_sessions', 'SELECT'), 'authenticated blocked';
    assert not has_function_privilege('anon', 'public.agent_session_command(text,text,jsonb)', 'EXECUTE'), 'RPC anon blocked';
    assert not has_function_privilege('authenticated', 'public.agent_session_command(text,text,jsonb)', 'EXECUTE'), 'RPC authenticated blocked';
    assert (select relrowsecurity from pg_class where oid = 'public.agent_search_sessions'::regclass), 'RLS enabled';
end $$;
rollback;
