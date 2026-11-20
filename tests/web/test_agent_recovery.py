"""Failure recovery uses only fake model/provider/store clients (PRD 5)."""
import json
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Event
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import pytest
from fastapi.testclient import TestClient

from retriever_lost_found.web.agent_api import create_agent_router
from retriever_lost_found.web.agent_search import Institution, PlaceCandidate, SearchPage
from retriever_lost_found.web.agent_store import MemoryAgentStore
from retriever_lost_found.web.app import create_app
from retriever_lost_found.integrations.kakao import KakaoLocalClient
from retriever_lost_found.integrations.agent_model import OpenRouterAgentModel


def decision():
    return dict(action='search', message='', place_query='서울역', item_query='지갑',
                clear_item_query=False, asks_for=None, item_changes=None, target=None)


def make_client(store=None, **overrides):
    params = dict(store=store if store is not None else MemoryAgentStore(), model=Mock(return_value=decision()),
                  find_places=lambda _: [PlaceCandidate('p', '서울역', '', 37.5, 127.)],
                  get_institutions=lambda: [Institution(42, 37.5, 127.)],
                  search_items=lambda *_: SearchPage(({'item_id': 1, 'institution_id': 42},), 1))
    params.update(overrides)
    return TestClient(create_app(Mock(), 'dummy', agent_router=create_agent_router(**params)))


def final(reply):
    assert reply.status_code == 200
    events = [json.loads(line) for line in reply.text.splitlines()]
    assert events[-1]['type'] == 'run.finished'
    return events[-1]['response']


def start(client):
    return final(client.post('/api/search-chat/turns', json=dict(type='message', text='private user text', request_id='first')))


def select(previous):
    return dict(type='select_place', request_id='select', session_id=previous['session_id'],
                revision=previous['revision'], candidate_id='p')


@pytest.mark.parametrize('stage', ['model', 'places', 'institutions', 'found_items'])
@pytest.mark.parametrize('error', [TimeoutError('private provider body'), ValueError('private schema')])
def test_failure_keeps_revision_and_can_retry_same_request(stage, error, caplog):
    store = MemoryAgentStore()
    model = Mock(side_effect=[error, decision()]) if stage == 'model' else Mock(return_value=decision())
    places = Mock(side_effect=[error, [PlaceCandidate('p', '서울역', '', 37.5, 127.)]]) if stage == 'places' else None
    institutions = Mock(side_effect=[error, [Institution(42, 37.5, 127.)]]) if stage == 'institutions' else None
    search = Mock(side_effect=[error, SearchPage(({'item_id': 1, 'institution_id': 42},), 1)]) if stage == 'found_items' else None
    overrides = dict(model=model)
    if places: overrides['find_places'] = places
    if institutions: overrides['get_institutions'] = institutions
    if search: overrides['search_items'] = search
    client = make_client(store, **overrides)
    request = dict(type='message', text='private user text', request_id='first')
    if stage in ('institutions', 'found_items'):
        previous = start(client)
        request = select(previous)
        old_revision = previous['revision']
    else:
        old_revision = 0
    failed = final(client.post('/api/search-chat/turns', json=request))
    assert failed['status'] == 'error'
    assert failed['revision'] == old_revision
    assert failed['error']['code'] == ('timeout' if isinstance(error, TimeoutError) else 'lookup_failed')
    succeeded = final(client.post('/api/search-chat/turns', json=request))
    assert succeeded['revision'] == old_revision + 1
    assert succeeded['status'] == ('results' if stage in ('institutions', 'found_items') else 'awaiting_place_selection')
    logs = [json.loads(row.message) for row in caplog.records if row.name == 'retriever.agent']
    failure = next(row for row in logs if row['event'] == 'turn.failed')
    assert failure['stage'] == stage
    assert failure['exception_type'] == type(error).__name__
    assert failure['trace_id'] == failed['trace_id']
    assert 'private' not in '\n'.join(row.message for row in caplog.records)


def test_initial_response_loss_replays_and_two_instances_continue_without_model_duplication():
    store = MemoryAgentStore()
    model = Mock(return_value=decision())
    a, b = make_client(store, model=model), make_client(store, model=model)
    first = start(a)  # Pretend the browser lost every byte, including session_id.
    recovered = start(b)
    assert recovered == first
    assert model.call_count == 1
    request = select(recovered)
    result = final(b.post('/api/search-chat/turns', json=request))
    assert result['status'] == 'results'
    assert final(a.post('/api/search-chat/turns', json=request)) == result
    assert a.post('/api/search-chat/turns', json={**request, 'candidate_id': 'wrong'}).status_code == 409
    # New request with old revision cannot run.
    assert a.post('/api/search-chat/turns', json={**request, 'request_id': 'new'}).status_code == 409


def test_inflight_duplicate_and_cancel_on_other_instance_cannot_commit_late_result():
    store = MemoryAgentStore()
    entered, release = Event(), Event()

    def blocking_search(*_):
        entered.set()
        assert release.wait(5)
        return SearchPage(({'item_id': 1, 'institution_id': 42},), 1)

    a, b = make_client(store, search_items=blocking_search), make_client(store)
    previous = start(a)
    request = select(previous)
    with ThreadPoolExecutor() as pool:
        future = pool.submit(a.post, '/api/search-chat/turns', json=request)
        assert entered.wait(5)
        row = store.command(previous['session_id'], 'get')
        duplicate = b.post('/api/search-chat/turns', json=request)
        assert duplicate.status_code == 409
        assert duplicate.json()['error']['code'] == 'busy'
        assert b.post('/api/search-chat/cancel', json=dict(session_id=previous['session_id'], run_id='wrong')).json() == {'accepted': False}
        assert b.post('/api/search-chat/cancel', json=dict(session_id=previous['session_id'], run_id=row['run_id'])).json() == {'accepted': True}
        release.set()
        cancelled = final(future.result(5))
    assert cancelled['status'] == 'cancelled'
    assert cancelled['revision'] == previous['revision']
    assert cancelled['conditions'] == previous['conditions']
    assert cancelled['matches'] is None
    assert final(b.post('/api/search-chat/turns', json=request))['status'] == 'cancelled'
    assert final(b.post('/api/search-chat/turns', json={**request, 'request_id': 'search-again'}))['status'] == 'results'


def test_dead_worker_lease_fences_late_completion_and_expiry():
    now = [100.]
    store = MemoryAgentStore(clock=lambda: now[0])
    args = dict(create=True, initial={'state': {}, 'status': '', 'cursor': None}, revision=0,
                request_id='r', fingerprint='f', run_id='old')
    assert store.command('s', 'claim', args)['outcome'] == 'claimed'
    assert store.command('s', 'claim', args)['outcome'] == 'busy'
    now[0] += 121
    assert store.command('s', 'claim', {**args, 'run_id': 'new'})['outcome'] == 'claimed'
    finish = dict(run_id='old', commit=True, data={}, response={'status': 'results', 'revision': 1}, previous={})
    assert store.command('s', 'finish', finish)['outcome'] == 'state_conflict'
    assert store.command('s', 'finish', {**finish, 'run_id': 'new'})['outcome'] == 'ok'
    now[0] += 1800
    assert store.command('s', 'get')['outcome'] == 'session_expired'
    assert store.rows == {}


def test_completion_wins_cancel_and_ended_session_rejects_new_turn():
    client = make_client()
    previous = start(client)
    request = dict(type='end', request_id='end', session_id=previous['session_id'], revision=previous['revision'])
    ended = final(client.post('/api/search-chat/turns', json=request))
    assert ended['status'] == 'ended'
    assert final(client.post('/api/search-chat/turns', json=request)) == ended
    assert client.post('/api/search-chat/cancel', json=dict(session_id=ended['session_id'], run_id=ended['run_id'])).json() == {'accepted': False}
    assert client.post('/api/search-chat/turns', json={**request, 'request_id': 'next', 'revision': ended['revision']}).status_code == 409


def test_kakao_429_and_model_schema_diagnostics_never_log_body(caplog):
    provider_error = HTTPError('https://private.invalid/?key=private', 429, 'private', {}, BytesIO(b'private response'))
    client = make_client(find_places=lambda _: KakaoLocalClient('private-key').search_keyword('private'))
    with patch('urllib.request.urlopen', side_effect=provider_error):
        failed = start(client)
    assert failed['error']['code'] == 'busy'
    assert any(json.loads(row.message).get('external_status') == 429 for row in caplog.records)
    caplog.clear()
    schema = make_client(model=Mock(return_value={'private': 'secret invalid schema'}))
    assert start(schema)['status'] == 'error'
    assert any(json.loads(row.message).get('exception_type') == 'ValidationError' for row in caplog.records)
    assert 'private' not in '\n'.join(row.message for row in caplog.records)


def test_transport_timeout_cause_survives_and_store_failure_is_recoverable():
    with patch('retriever_lost_found.integrations.agent_model.build_opener') as opener:
        opener.return_value.open.side_effect = TimeoutError('private')
        client = make_client(model=OpenRouterAgentModel('dummy'))
        assert start(client)['error']['code'] == 'timeout'
    store = MemoryAgentStore()
    real = store.command
    fail = [True]

    def flaky(session_id, command, args=None):
        if command == 'finish' and fail[0]:
            fail[0] = False
            real(session_id, command, args)  # Committed, but transport lost the reply.
            raise TimeoutError('private')
        return real(session_id, command, args)

    store.command = flaky
    model = Mock(return_value=decision())
    client = make_client(store, model=model)
    assert start(client)['error']['code'] == 'state_unavailable'
    assert start(client)['status'] == 'awaiting_place_selection'
    assert model.call_count == 1


def test_closed_stream_does_not_save_partial_conversation(monkeypatch):
    import asyncio
    from retriever_lost_found.web import agent_api
    from starlette.requests import Request

    store = MemoryAgentStore()
    model = Mock(return_value=decision())
    router = create_agent_router(store=store, model=model, find_places=lambda _: [],
                                 get_institutions=lambda: [], search_items=lambda *_: SearchPage((), 0))
    request = dict(type='message', request_id='disconnect', text='private text')
    raw = json.dumps(request).encode()

    async def receive():
        return {'type': 'http.request', 'body': raw, 'more_body': False}

    # Capture the actual sync iterator passed to Starlette; close it after the
    # model updates local state but before the place tool returns (disconnect).
    monkeypatch.setattr(agent_api, 'StreamingResponse', lambda content, **_: content)
    endpoint = next(route.endpoint for route in router.routes if route.path.endswith('/turns'))
    stream = asyncio.run(endpoint(Request({'type': 'http', 'method': 'POST', 'path': '/'}, receive)))
    assert json.loads(next(stream))['type'] == 'run.started'
    assert json.loads(next(stream))['tool'] == 'places'
    stream.close()
    row = store.command(store.session_id('disconnect'), 'get')
    assert row['revision'] == 0 and row['run_id'] is None
    assert row['data']['state']['place_query'] is None
    assert row['data']['state']['recent'] == ()


def test_embedding_fallback_keeps_trace_across_search_worker(caplog):
    from retriever_lost_found.agent_diagnostics import trace_id
    from retriever_lost_found.search.hybrid import search_with_deadline

    class Items:
        def fetch_items(self, scope):
            return [{'id': 1, 'storage_location_id': 42, 'item_name': '지갑'}]

        def fetch_rerank_items(self, scope, ids, **kwargs):
            return [{'detail': self.fetch_items(scope)[0], 'vector': None}]

    class BrokenEmbedding:
        def embed(self, *args, **kwargs):
            raise TimeoutError('private')

    token = trace_id.set('trace-test')
    try:
        items, _, status = search_with_deadline(Items(), '지갑', [42], BrokenEmbedding())
    finally:
        trace_id.reset(token)
    assert status == 'unavailable' and items[0]['id'] == 1
    event = next(json.loads(row.message) for row in caplog.records if json.loads(row.message)['event'] == 'embedding.fallback')
    assert event['trace_id'] == 'trace-test' and event['reason'] == 'timeout'
    assert 'private' not in '\n'.join(row.message for row in caplog.records)


def test_supabase_transport_uses_server_credentials_and_stable_initial_identity():
    from retriever_lost_found.web.agent_store import SupabaseAgentStore

    a = SupabaseAgentStore('https://example.invalid', 'private-key')
    b = SupabaseAgentStore('https://example.invalid', 'private-key')
    other = SupabaseAgentStore('https://example.invalid', 'different-key')
    assert a.session_id('first') == b.session_id('first')
    assert a.session_id('first') != other.session_id('first')
    with patch('urllib.request.urlopen', return_value=BytesIO(b'{"outcome":"session_expired"}')) as send:
        assert a.command('session', 'get') == {'outcome': 'session_expired'}
    request = send.call_args.args[0]
    assert request.full_url.endswith('/rest/v1/rpc/agent_session_command')
    assert json.loads(request.data) == {'p_session_id': 'session', 'p_command': 'get', 'p_args': {}}
    assert send.call_args.kwargs['timeout'] == 3
    assert request.get_header('Apikey') == 'private-key'
