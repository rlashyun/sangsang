"""PRD 4.5 — successful batches survive failure and the next slice resumes."""
from hashlib import sha256

from retriever_lost_found.embedding.service import run_slice
from retriever_lost_found.integrations.embeddings import EmbeddingRequestError


class Store:
    def __init__(self):
        self.source = {i: f"검정 지갑 {i}" for i in range(1, 66)}
        self.saved = {1: "previous input stays unchanged"}
        self.cursor = 0
        self.failed = 0

    def batch(self, run, after):
        return [dict(item_id=i, embedded_input=t, content_hash=sha256(t.encode()).hexdigest())
                for i, t in self.source.items() if i > after and i not in self.saved][:64]

    def save(self, run, records):
        for r in records:
            self.saved.setdefault(r["item_id"], r["embedded_input"])
        return len(records)

    def checkpoint(self, run, after, saved, failed):
        self.cursor = after
        self.failed += failed

    def finish(self, run, exhausted, error=None):
        remaining = len(set(self.source) - set(self.saved))
        return dict(status="succeeded" if not remaining else "partial", remaining_count=remaining)


class Provider:
    def embed_many(self, texts, **kwargs):
        if any(t.endswith(" 34") for t in texts):
            raise EmbeddingRequestError("invalid_response")
        return [[0.5] * 1024 for _ in texts]


def test_successful_batch_remains_saved_after_another_batch_fails():
    store = Store()
    result = run_slice(store, Provider(), {"id": 1, "after_id": 0}, budget=60)
    assert result["status"] == "partial"
    assert result["remaining_count"] == 32
    assert len(store.saved) == 33
    assert store.saved[1] == "previous input stays unchanged"
    assert store.failed == 32

    class Recovered:
        def embed_many(self, texts, **kwargs):
            return [[0.5] * 1024 for _ in texts]

    result = run_slice(store, Recovered(), {"id": 1, "after_id": 0}, budget=60)
    assert result["status"] == "succeeded"
    assert len(store.saved) == 65
    assert store.saved[1] == "previous input stays unchanged"


def test_deadline_does_not_start_work_that_cannot_finish():
    store = Store()
    result = run_slice(store, Provider(), {"id": 1, "after_id": 0}, budget=0)
    assert result["remaining_count"] == 64
    assert len(store.saved) == 1


def test_invalid_credentials_stop_before_draining_the_entire_backlog():
    class Unauthorized:
        def embed_many(self, texts, **kwargs):
            raise EmbeddingRequestError("HTTP_401")
    store = Store()
    store.source.update({i: f"검정 지갑 {i}" for i in range(66, 1001)})
    run_slice(store, Unauthorized(), {"id": 1, "after_id": 0}, budget=60)
    assert store.failed == 64
    assert store.cursor == 65


def test_source_preflight_failure_still_gets_a_terminal_audit():
    from datetime import date
    from unittest.mock import Mock
    from retriever_lost_found.ingestion.service import _sync_source
    store = Mock(secret_key="test-only")
    store.create_run.return_value = 7
    store.latest_registered_on.side_effect = RuntimeError("temporary database failure")
    result = _sync_source(store, source="police", today=date(2026, 11, 13), incremental_only=True)
    assert result["status"] == "failed"
    audit = store.finish_run.call_args.args[1]
    assert audit["status"] == "failed"
    assert audit["metadata"]["business_date"] == "2026-11-13"
    assert audit["metadata"]["upsert_completed"] is False


def test_recovery_continues_after_an_older_job_completes(monkeypatch):
    from unittest.mock import Mock, patch
    from retriever_lost_found.embedding.orchestration import process_pending_embeddings
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only")
    store = Mock()
    store.claim.return_value = {"id": 1, "business_date": "2026-11-12"}
    with patch("retriever_lost_found.embedding.orchestration.EmbeddingStore.from_environment", return_value=store), \
         patch("retriever_lost_found.embedding.orchestration.run_slice", return_value={"status": "succeeded"}), \
         patch("retriever_lost_found.embedding.orchestration.dispatch_embeddings") as dispatch:
        process_pending_embeddings()
    dispatch.assert_called_once()
