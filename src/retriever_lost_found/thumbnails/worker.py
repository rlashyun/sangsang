"""PRD 3.2-2 · 5 — 중단 가능한 축소본 작업과 삭제 정리."""
from __future__ import annotations

import logging
import time
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

from ..config import read_env_value, read_optional_env_value
from ..ingestion.store import SupabaseIngestionStore
from .storage import ThumbnailStorage, _NoRedirect


class ThumbnailStore(SupabaseIngestionStore):
    def rpc(self, name, payload):
        return self._request("POST", "rpc/" + name, payload=payload)


def run_pass(store, storage, *, limit=60, deadline_seconds=220, clock=time.monotonic, sleep=time.sleep):
    start = clock()
    # 백필은 수집·검색에 긴 잠금을 걸지 않도록 작은 페이지로 진행한다.
    backfill_complete = store.rpc("thumbnail_backfill_next", {"p_limit": 500})
    completed = failed = 0
    for _ in range(limit):
        if clock() - start >= deadline_seconds:
            break
        rows = store.rpc("thumbnail_claim", {})
        if not rows:
            break
        task = rows[0]
        success = False
        try:
            if task["deleted_at"] is not None:
                storage.delete(task["object_key"])
            else:
                key = storage.create(int(task["item_id"]), task["source_url"])
                if key != task["object_key"]:
                    raise ValueError("축소 사진 경로가 일치하지 않습니다.")
            success = True
            completed += 1
        except Exception:
            # 주소, 사진 및 비밀값을 로그에 남기지 않는다.
            failed += 1
            logging.getLogger(__name__).warning("thumbnail task failed")
        store.rpc("thumbnail_finish", {"p_item_id": task["item_id"], "p_key": task["object_key"],
                                      "p_token": task["lease_token"], "p_success": success})
        # PRD 5 — 원천 서버와 DB에 일괄 요청을 집중하지 않는다.
        sleep(0.5)
    return {"status": "completed", "processed": completed, "failed": failed,
            "backfill_complete": bool(backfill_complete)}


def dispatch_thumbnails():
    # PRD 5 — Preview나 로컬에서 운영 작업을 시작하지 않는다.
    if read_optional_env_value("VERCEL_ENV") != "production":
        return False
    domain = read_optional_env_value("VERCEL_PROJECT_PRODUCTION_URL")
    secret = read_optional_env_value("CRON_SECRET")
    if not domain or not secret:
        return False
    target = "https://" + domain + "/api/cron/thumbnail-sync"
    try:
        parts = urlsplit(target)
        valid = (parts.scheme == "https" and bool(parts.hostname) and not parts.username and not parts.password
                 and parts.path == "/api/cron/thumbnail-sync" and not parts.query and not parts.fragment
                 and parts.port in (None, 443))
    except ValueError:
        return False
    if not valid:
        return False
    request = Request(target, data=b"{}", method="POST",
                      headers={"Authorization": "Bearer " + secret, "Content-Type": "application/json"})
    try:
        with build_opener(_NoRedirect()).open(request, timeout=2) as response:
            return response.status == 202
    except Exception:
        logging.getLogger(__name__).warning("thumbnail dispatch deferred")
        return False


def process_pending():
    url = read_env_value("SUPABASE_URL")
    secret = read_optional_env_value("SUPABASE_SECRET_KEY") or read_env_value("SUPABASE_SERVICE_ROLE_KEY")
    store = ThumbnailStore(url, secret)
    token = store.rpc("thumbnail_worker_claim", {})
    if not token:
        return {"status": "already_running"}
    try:
        result = run_pass(store, ThumbnailStorage(url, secret))
    finally:
        store.rpc("thumbnail_worker_release", {"p_token": token})
    if result["processed"] or result["failed"] or not result["backfill_complete"]:
        # 빈 백필 페이지에도 호출 간격을 둔다. 실패하면 다음 정기 수집에서 재개한다.
        if not (result["processed"] or result["failed"]):
            time.sleep(3)
        dispatch_thumbnails()
    return result
