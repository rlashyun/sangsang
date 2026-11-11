"""Server-only BGE-M3 client; same model/provider as stored item vectors."""
from __future__ import annotations

import json
import math
from collections import OrderedDict
from threading import Lock
from time import monotonic
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class BgeM3Client:
    def __init__(self, api_key: str, *, timeout: float = 5):
        self.api_key = api_key
        self.timeout = timeout
        self._cache = OrderedDict()
        self._cache_lock = Lock()

    def embed(self, text: str, *, timeout: float | None = None) -> list[float]:
        # Query vectors only: no caching of found-item responses or failures.
        with self._cache_lock:
            cached = self._cache.get(text)
            if cached is not None and monotonic()-cached[0] < 3600:
                self._cache.move_to_end(text)
                return list(cached[1])
        request = Request(
            "https://openrouter.ai/api/v1/embeddings",
            data=json.dumps({"model": "baai/bge-m3", "input": [text],
                             "provider": {"only": ["deepinfra"], "allow_fallbacks": False}}).encode(),
            headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with build_opener(NoRedirect()).open(request, timeout=min(self.timeout, timeout) if timeout is not None else self.timeout) as response:
                payload = json.load(response)
            records = payload["data"]
            vector = records[0]["embedding"]
            if (payload.get("model", "baai/bge-m3").lower() != "baai/bge-m3"
                or payload.get("provider", "deepinfra").lower() != "deepinfra"
                or len(records) != 1 or records[0].get("index") != 0
                or not isinstance(vector, list) or len(vector) != 1024
                or not all(type(v) in (int, float) and math.isfinite(v) for v in vector)
                or not any(vector)):
                raise ValueError
            with self._cache_lock:
                self._cache[text] = (monotonic(), tuple(vector))
                self._cache.move_to_end(text)
                if len(self._cache)>128:
                    self._cache.popitem(last=False)
            return vector
        except (HTTPError, URLError, OSError, HTTPException, ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise RuntimeError("의미 검색용 임베딩을 생성하지 못했습니다.") from None
