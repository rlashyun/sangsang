"""Allowlisted diagnostics: never render exceptions, prompts, URLs or payloads."""
import logging
import json
from contextvars import ContextVar
from time import monotonic
from urllib.error import HTTPError, URLError
from uuid import uuid4

trace_id = ContextVar('agent_trace_id', default=None)
request_started = ContextVar('agent_request_started', default=None)
logger = logging.getLogger('retriever.agent')
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(handler)


def start_trace():
    trace_id.set(uuid4().hex)
    request_started.set(monotonic())


def failure_fields(error: Exception) -> dict:
    current = error
    visited = set()
    code = None
    timeout = False
    reason = None
    while id(current) not in visited:
        visited.add(id(current))
        if getattr(current, "failure_reason", None) in ("busy", "timeout"):
            reason = current.failure_reason
        timeout |= isinstance(current, TimeoutError)
        if isinstance(current, HTTPError):
            code = current.code
        if isinstance(current, URLError) and isinstance(current.reason, TimeoutError):
            timeout = True
        cause = current.__cause__ or current.__context__
        if not isinstance(cause, Exception):
            break
        current = cause
    return {'exception_type': type(current).__name__, 'external_status': code,
            'reason': reason or ('timeout' if timeout else 'busy' if code == 429 else 'lookup_failed')}


def record(event: str, *, started: float | None = None, error: Exception | None = None, **fields):
    # PRD 5 — Values are supplied only from server-owned stages/statuses.
    started = started if started is not None else request_started.get()
    logger.info(json.dumps({
        'event': event, 'trace_id': trace_id.get(),
        **({'elapsed_ms': round((monotonic() - started) * 1000, 2)} if started is not None else {}),
        **(failure_fields(error) if error else {}), **fields,
    }, ensure_ascii=False))
