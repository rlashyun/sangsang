"""PRD 4.5 / 5 — completion dispatch is best effort; DB jobs survive lost delivery."""
from datetime import datetime
import json
import logging
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

from ..config import read_optional_env_value
from ..ingestion.collector import KOREA_TIMEZONE
from ..integrations.embeddings import BgeM3Client, NoRedirect
from .service import run_slice
from .store import EmbeddingStore


def dispatch_embeddings():
    target = read_optional_env_value("EMBEDDING_WORKER_URL")
    if not target:
        domain = read_optional_env_value("VERCEL_PROJECT_PRODUCTION_URL")
        if domain:
            target = "https://" + domain + "/api/cron/embedding-sync"
    secret = read_optional_env_value("CRON_SECRET")
    if not target or not secret:
        return False
    parsed = urlsplit(target)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path != "/api/cron/embedding-sync" or parsed.query or parsed.fragment):
        return False
    request = Request(target, data=b"{}", method="POST", headers={
        "Authorization": "Bearer " + secret, "Content-Type": "application/json"})
    try:
        with build_opener(NoRedirect()).open(request, timeout=2) as response:
            return response.status == 202
    except Exception:
        # Never log request objects, URLs, exception messages, headers or bodies.
        logging.getLogger(__name__).warning("embedding dispatch deferred to recovery")
        return False


def process_pending_embeddings(*, recover=False):
    store = EmbeddingStore.from_environment()
    today = datetime.now(KOREA_TIMEZONE).date()
    try:
        if recover:
            store.recover(today)
        store.ensure(today)
        run = store.claim()
        if run is None:
            return {"status": "waiting_or_idle"}
        key = read_optional_env_value("OPENROUTER_API_KEY")
        if not key:
            return store.finish(run, False, "missing_api_key")
        result = run_slice(store, BgeM3Client(key), run)
        if result["status"] in ("pending", "succeeded"):
            dispatch_embeddings()
        return result
    except Exception:
        logging.getLogger(__name__).warning("embedding worker interrupted; lease recovery required")
        return {"status": "failed", "error": "임베딩 작업을 완료하지 못했습니다."}
