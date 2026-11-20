"""Atomic search-session storage; no provider payloads or secrets are persisted."""

from copy import deepcopy
from threading import Lock
from time import time
from typing import Any, Protocol
import json
import hmac
from secrets import token_bytes
from urllib.request import Request

from ..integrations.supabase import _headers, _request_json

TTL_SECONDS = 1800
LEASE_SECONDS = 120


class AgentStore(Protocol):
    def session_id(self, request_id: str) -> str: ...
    def command(self, session_id: str, command: str, args: dict | None = None) -> dict[str, Any]: ...


class MemoryAgentStore:
    """Explicit local/test implementation of the shared-store command contract."""

    def __init__(self, clock=time):
        self.identity_key = token_bytes(32)
        self.clock = clock
        self.rows: dict[str, dict] = {}
        self.lock = Lock()

    def session_id(self, request_id: str) -> str:
        return hmac.digest(self.identity_key, request_id.encode(), 'sha256').hex()

    def command(self, session_id: str, command: str, args: dict | None = None) -> dict[str, Any]:
        args = args or {}
        with self.lock:
            now = self.clock()
            self.rows = {key: row for key, row in self.rows.items() if row['expires'] > now}
            row = self.rows.get(session_id)
            if row is None and command == 'claim' and args.get('create'):
                if len(self.rows) >= 128:
                    return {'outcome': 'session_capacity'}
                row = self.rows[session_id] = {
                    'data': args['initial'], 'revision': 0, 'expires': now + TTL_SECONDS,
                    'run_id': None, 'cancelled': False, 'request_id': None,
                    'fingerprint': None, 'response': None, 'seen': [],
                }
            if row is None:
                return {'outcome': 'session_expired'}
            if command == 'get':
                return deepcopy({'outcome': 'ok', **row})
            if command == 'claim':
                same = row['request_id'] == args['request_id']
                if same and row['fingerprint'] != args['fingerprint']:
                    return {'outcome': 'state_conflict'}
                if same and row['response'] is not None:
                    row['expires'] = now + TTL_SECONDS
                    return deepcopy({'outcome': 'replay', 'response': row['response']})
                if row['data'].get('status') == 'ended':
                    return {'outcome': 'state_conflict'}
                if row['run_id'] and row['lease'] > now:
                    return {'outcome': 'busy'}
                if row['revision'] != args['revision'] or (not same and args['request_id'] in row['seen']):
                    return {'outcome': 'state_conflict'}
                if not same and len(row['seen']) >= 100:
                    return {'outcome': 'session_expired'}
                row.update(run_id=args['run_id'], request_id=args['request_id'], fingerprint=args['fingerprint'],
                           response=None, cancelled=False, lease=now + LEASE_SECONDS, expires=now + TTL_SECONDS)
                if not same:
                    row['seen'] = [*row['seen'], args['request_id']]
                return deepcopy({'outcome': 'claimed', **row})
            if command == 'cancel':
                accepted = row['run_id'] == args['run_id'] and row['lease'] > now
                if accepted:
                    row['cancelled'] = True
                return {'outcome': 'ok', 'accepted': accepted}
            if command == 'check':
                return {'outcome': 'ok', 'cancelled': row['run_id'] != args['run_id'] or row['cancelled'] or row['lease'] <= now}
            if command == 'finish':
                if row['run_id'] != args['run_id'] or row['lease'] <= now:
                    return {'outcome': 'state_conflict'}
                response = deepcopy(args['response'])
                if row['cancelled'] or not args['commit']:
                    if row['cancelled']:
                        response.update(status='cancelled', message='검색을 취소했어요.', error=None)
                    response.update(revision=row['revision'], **args['previous'])
                    response.update(matches=None, recommended_item_ids=[], search_attempts=[])
                else:
                    row['data'] = args['data']
                    row['revision'] = response['revision']
                row.update(run_id=None, expires=now + TTL_SECONDS)
                # Failed attempts can retry; explicit cancellation is a replayable terminal outcome.
                row['response'] = response if response['status'] != 'error' else None
                return {'outcome': 'ok', 'response': response}
            if command == 'page':
                if row['run_id'] or row['revision'] != args['revision'] or row['data'].get('cursor') != args['cursor']:
                    return {'outcome': 'state_conflict'}
                row['data']['cursor'] = args['next_cursor']
                cached = {**row['data'].get('items', {}), **args.get('items', {})}
                row['data']['items'] = dict(list(cached.items())[-100:])
                row['expires'] = now + TTL_SECONDS
                return {'outcome': 'ok'}
            raise ValueError('Unknown session command')


class SupabaseAgentStore:
    """Use only the existing server-side Supabase credentials (PRD 5)."""

    def __init__(self, url: str, secret_key: str):
        self.url, self.secret_key = url.rstrip('/'), secret_key

    def session_id(self, request_id: str) -> str:
        return hmac.digest(self.secret_key.encode(), ('agent-session:' + request_id).encode(), 'sha256').hex()

    def command(self, session_id: str, command: str, args: dict | None = None) -> dict:
        request = Request(
            f'{self.url}/rest/v1/rpc/agent_session_command',
            data=json.dumps({'p_session_id': session_id, 'p_command': command, 'p_args': args or {}}).encode(),
            headers=_headers(self.secret_key, content_type=True), method='POST',
        )
        result = _request_json(request, 3, '대화 상태 저장소')
        if not isinstance(result, dict) or 'outcome' not in result:
            raise RuntimeError('대화 상태 저장소 응답 형식 오류')
        return result
