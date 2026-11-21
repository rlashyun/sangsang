from unittest.mock import patch
from threading import Barrier, Lock

from retriever_lost_found.thumbnails.worker import dispatch_thumbnails, process_pending, run_pass


class FakeStore:
    def __init__(self, tasks):
        self.tasks = list(tasks)
        self.calls = []

    def rpc(self, name, data):
        self.calls.append((name, data))
        if name == "thumbnail_claim":
            return [self.tasks.pop(0)] if self.tasks else []
        return None


class FakeStorage:
    def __init__(self):
        self.created = []
        self.deleted = []

    def create(self, item_id, url):
        self.created.append((item_id, url))
        if url == "bad":
            raise ValueError("failed")
        return "key"

    def delete(self, key):
        self.deleted.append(key)


def task(item_id, url, deleted=False):
    return {"item_id": item_id, "source_url": url, "deleted_at": "now" if deleted else None,
            "object_key": "key", "lease_token": "token"}


def test_worker_records_success_failure_and_deleted_cleanup():
    store = FakeStore([task(1, "ok"), task(2, "bad"), task(3, "old", deleted=True)])
    storage = FakeStorage()
    result = run_pass(store, storage, sleep=lambda _: None)
    assert result == {"status": "completed", "processed": 2, "failed": 1, "backfill_complete": False}
    assert storage.created == [(1, "ok"), (2, "bad")]
    assert storage.deleted == ["key"]
    assert [args["p_success"] for name, args in store.calls if name == "thumbnail_finish"] == [True, False, True]


def test_worker_obeys_task_limit():
    store = FakeStore([task(1, "ok"), task(2, "ok")])
    assert run_pass(store, FakeStorage(), limit=1, sleep=lambda _: None)["processed"] == 1
    assert len(store.tasks) == 1


def test_two_photos_are_processed_concurrently_and_completed():
    barrier = Barrier(2, timeout=3)
    lock = Lock()
    active = 0
    maximum = 0

    class ConcurrentStorage(FakeStorage):
        def create(self, item_id, url):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            barrier.wait()
            with lock:
                active -= 1
            return "key"

    store = FakeStore([task(1, "ok"), task(2, "ok")])
    result = run_pass(store, ConcurrentStorage(), sleep=lambda _: None)
    assert maximum == 2
    assert result["processed"] == 2
    assert result["failed"] == 0
    assert len([name for name, _ in store.calls if name == "thumbnail_finish"]) == 2


def test_process_does_not_run_a_second_worker():
    store = FakeStore([])
    with patch("retriever_lost_found.thumbnails.worker.read_env_value", return_value="test"), \
         patch("retriever_lost_found.thumbnails.worker.read_optional_env_value", return_value="test"), \
         patch("retriever_lost_found.thumbnails.worker.ThumbnailStore", return_value=store), \
         patch("retriever_lost_found.thumbnails.worker.run_pass") as run:
        assert process_pending() == {"status": "already_running"}
        run.assert_not_called()


def test_preview_never_dispatches_to_production():
    with patch.dict("os.environ", {"VERCEL_ENV": "preview", "VERCEL_PROJECT_PRODUCTION_URL": "production.example",
                                  "CRON_SECRET": "secret"}), \
         patch("retriever_lost_found.thumbnails.worker.build_opener") as opener:
        assert dispatch_thumbnails() is False
        opener.assert_not_called()
