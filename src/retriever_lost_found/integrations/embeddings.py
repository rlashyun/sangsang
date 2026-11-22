"""Server-only embedding clients. Gemini Embedding 2: 검색어(GeminiQueryClient)·일일 물품 동기화(GeminiItemClient).
BgeM3Client는 BGE 표 유지보수(privacy/reembed.py)용으로만 남는다(2026-11-22)."""
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


class GeminiQueryClient:
    """PRD 4.5 — 검색어 전용 Gemini Embedding 2 클라이언트(2026-11-22 전환).

    물품 벡터(found_item_embeddings_gemini2)와 같은 모델·경로·차원이다. 검색어는 Google 권장 검색 형식
    'task: search result | query: …'로 감싼다. 물품 임베딩(일일 동기화)은 GeminiItemClient가 맡는다.
    """
    MODEL = "google/gemini-embedding-2"
    PROVIDER = "Google AI Studio"
    QUERY_FORMAT = "task: search result | query: {}"

    def __init__(self, api_key: str, *, timeout: float = 5):
        self.api_key = api_key
        self.timeout = timeout
        self._cache = OrderedDict()
        self._cache_lock = Lock()

    def embed(self, text: str, *, timeout: float | None = None) -> list[float]:
        # Query vectors only: no caching of found-item responses or failures.
        if not isinstance(text, str) or not text.strip():
            raise ValueError("검색어가 비어 있습니다.")
        with self._cache_lock:
            cached = self._cache.get(text)
            if cached is not None and monotonic()-cached[0] < 3600:
                self._cache.move_to_end(text)
                return list(cached[1])
        try:
            vector = self._request(text, min(self.timeout, timeout) if timeout is not None else self.timeout)
        except EmbeddingRequestError:
            raise RuntimeError("의미 검색용 임베딩을 생성하지 못했습니다.") from None
        with self._cache_lock:
            self._cache[text] = (monotonic(), tuple(vector))
            self._cache.move_to_end(text)
            if len(self._cache)>128:
                self._cache.popitem(last=False)
        return vector

    def _request(self, text: str, timeout: float) -> list[float]:
        request = Request(
            "https://openrouter.ai/api/v1/embeddings",
            data=json.dumps({"model": self.MODEL, "input": [self.QUERY_FORMAT.format(text)], "dimensions": 1024,
                             "encoding_format": "float",
                             "provider": {"only": [self.PROVIDER], "allow_fallbacks": False,
                                          "data_collection": "deny"}}).encode(),
            headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
                payload = json.load(response)
            records = payload["data"]
            model = str(payload.get("model", "gemini-embedding-2")).lower()
            if (model not in ("gemini-embedding-2", "google/gemini-embedding-2", "models/gemini-embedding-2")
                or str(payload.get("provider", self.PROVIDER)).lower() != self.PROVIDER.lower()
                or not isinstance(records, list) or len(records) != 1 or records[0].get("index") != 0):
                raise ValueError
            vector = records[0]["embedding"]
            if (not isinstance(vector, list) or len(vector) != 1024
                    or not all(type(x) in (int, float) and math.isfinite(x) and abs(x) <= 65504 for x in vector)
                    or not any(vector)):
                raise ValueError
            return vector
        except HTTPError as error:
            raise EmbeddingRequestError("HTTP_" + str(error.code),
                retryable=error.code in (408, 429) or error.code >= 500) from None
        except (URLError, OSError, HTTPException):
            raise EmbeddingRequestError("network", retryable=True) from None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise EmbeddingRequestError("invalid_response") from None


class GeminiItemClient:
    """PRD 4.5 — 일일 물품 임베딩용 Gemini Embedding 2 클라이언트(2026-11-22 크론 전환, 글만).

    일괄 적재와 같은 입력 'title: none | text: {마스킹한 embedding_input}'·같은 모델·경로·1024차원이다.
    응답 순서·개수·모델·경로·값을 모두 확인하고, 실패는 안전한 분류만 EmbeddingRequestError로 낸다. 캐시하지 않는다.
    """
    MODEL = GeminiQueryClient.MODEL
    PROVIDER = GeminiQueryClient.PROVIDER
    DOCUMENT_FORMAT = "title: none | text: {}"

    def __init__(self, api_key: str):
        self.api_key = api_key

    def embed_many(self, texts: list[str], *, timeout: float = 30) -> list[list[float]]:
        if not texts:
            return []
        if len(texts) > 32 or any(not isinstance(t, str) or not t.strip() for t in texts):
            raise ValueError("임베딩 배치는 비어 있지 않은 문장 최대 32개입니다.")
        request = Request(
            "https://openrouter.ai/api/v1/embeddings",
            data=json.dumps({"model": self.MODEL, "input": [self.DOCUMENT_FORMAT.format(t) for t in texts],
                             "dimensions": 1024, "encoding_format": "float",
                             "provider": {"only": [self.PROVIDER], "allow_fallbacks": False,
                                          "data_collection": "deny"}}).encode(),
            headers={"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
                payload = json.load(response)
            records = payload["data"]
            model = str(payload.get("model", "gemini-embedding-2")).lower()
            if (model not in ("gemini-embedding-2", "google/gemini-embedding-2", "models/gemini-embedding-2")
                or str(payload.get("provider", self.PROVIDER)).lower() != self.PROVIDER.lower()
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
