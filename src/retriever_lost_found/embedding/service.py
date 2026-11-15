"""PRD 4.5 / 5 — two bounded API workers; committed batches are never replayed."""
from concurrent.futures import ThreadPoolExecutor
import json
import time

from ..integrations.embeddings import EmbeddingRequestError


def _generate(client, rows, deadline):
    for attempt in range(3):
        remaining = deadline - time.monotonic()
        if remaining <= 2:
            raise EmbeddingRequestError("deadline", retryable=True)
        try:
            vectors = client.embed_many([r["embedded_input"] for r in rows],
                                       timeout=min(30, remaining - 1))
            return [{**row, "embedding": json.dumps(vector, separators=(",", ":")),
                     "vector_source": "openrouter_half_v2"} for row, vector in zip(rows, vectors, strict=True)]
        except EmbeddingRequestError as error:
            delay = max(error.retry_after, 2 * (attempt + 1))
            if not error.retryable or attempt == 2 or time.monotonic() + delay + 2 >= deadline:
                raise
            time.sleep(delay)


def run_slice(store, client, run, *, budget=240, report=None, capture=None):
    """Lease state is authoritative. All failures leave missing rows resumable."""
    deadline = time.monotonic() + min(budget, 240)
    store.deadline = deadline + 30  # Includes final DB checkpoint/audit; below Vercel's 300s.
    after = run.get("after_id", 0)
    exhausted = False
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="item-embedding") as pool:
        while time.monotonic() + 2 < deadline:
            try:
                rows = store.batch(run, after)
            except RuntimeError:
                return store.finish(run, False, "database_or_ingestion")
            if not rows:
                exhausted = True
                break
            batches = [rows[i:i+32] for i in range(0, len(rows), 32)]
            futures = [pool.submit(_generate, client, batch, deadline) for batch in batches]
            saved = failed = 0
            fatal_error = None
            for batch, future in zip(batches, futures):
                try:
                    records = future.result()
                    if capture is not None:
                        capture(records)
                    saved += store.save(run, records)
                except EmbeddingRequestError as error:
                    failed += len(batch)
                    if error.reason in ("HTTP_401", "HTTP_402", "HTTP_403"):
                        fatal_error = error.reason
                except RuntimeError:
                    # Never advance the checkpoint over a failed DB transaction.
                    return store.finish(run, False, "database")
            after = max(row["item_id"] for row in rows)
            store.checkpoint(run, after, saved, failed)
            if report:
                report({"run_id": run["id"], "after_id": after, "saved": saved, "failed": failed})
            if fatal_error:
                return store.finish(run, False, fatal_error)
    return store.finish(run, exhausted)
