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


class EmbeddingRequestError(RuntimeError):
    """Only safe classifications escape the provider boundary (PRD 5)."""
    def __init__(self, reason: str, *, retryable: bool = False, retry_after: float = 0):
        super().__init__("임베딩 생성 실패: " + reason)
        self.reason = reason
        self.retryable = retryable
        self.retry_after = retry_after


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
        try:
            vector = self.embed_many([text], timeout=min(self.timeout, timeout) if timeout is not None else self.timeout)[0]
        except EmbeddingRequestError:
            raise RuntimeError("의미 검색용 임베딩을 생성하지 못했습니다.") from None
        with self._cache_lock:
            self._cache[text] = (monotonic(), tuple(vector))
            self._cache.move_to_end(text)
            if len(self._cache)>128:
                self._cache.popitem(last=False)
        return vector

    def embed_many(self, texts: list[str], *, timeout: float = 30) -> list[list[float]]:
        """PRD 4.5 — uncached item batches; reject incomplete or reordered-invalid data."""
        if not texts:
            return []
        if len(texts) > 32 or any(not isinstance(t, str) or not t.strip() for t in texts):
            raise ValueError("임베딩 배치는 비어 있지 않은 문장 최대 32개입니다.")
        request = Request(
            "https://openrouter.ai/api/v1/embeddings",
            data=json.dumps({"model": "baai/bge-m3", "input": texts,
                             "provider": {"only": ["deepinfra"], "allow_fallbacks": False}}).encode(),
            headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
                payload = json.load(response)
            records = payload["data"]
            if (payload.get("model", "baai/bge-m3").lower() != "baai/bge-m3"
                or payload.get("provider", "deepinfra").lower() != "deepinfra"
                or not isinstance(records, list) or len(records) != len(texts)
                or any(type(r.get("index")) is not int for r in records)
                or {r["index"] for r in records} != set(range(len(texts)))):
                raise ValueError
            vectors = [r["embedding"] for r in sorted(records, key=lambda r: r["index"])]
            if any(not isinstance(v, list) or len(v) != 1024
                   or not all(type(x) in (int, float) and math.isfinite(x) and abs(x) <= 65504 for x in v)
                   or not any(v) for v in vectors):
                raise ValueError
            return vectors
        except HTTPError as error:
            try:
                delay = max(0, min(60, float(error.headers.get("Retry-After", "0"))))
            except (ValueError, TypeError, AttributeError):
                delay = 0
            raise EmbeddingRequestError("HTTP_" + str(error.code),
                retryable=error.code in (408, 429) or error.code >= 500, retry_after=delay) from None
        except (URLError, OSError, HTTPException):
            raise EmbeddingRequestError("network", retryable=True) from None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise EmbeddingRequestError("invalid_response") from None
