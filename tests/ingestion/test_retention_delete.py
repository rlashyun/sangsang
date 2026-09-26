"""보존기간 삭제가 statement_timeout 안에서 끝나도록 배치로 쪼개지는지 확인합니다.

근거 — PRD 4.5(보존기간 삭제) · PRD 5 신뢰성.
전량 단일 DELETE는 found_items가 커지면 Postgres statement_timeout(57014)에 걸려
`ingestion_runs.status`가 매일 `partial`로 남고 deleted_count가 0이 된다.
"""

from __future__ import annotations

import unittest
from typing import Any

from retriever_lost_found.ingestion.store import (
    DELETE_BATCH_SIZE,
    DELETE_MAX_BATCHES,
    SupabaseIngestionStore,
)


class RecordingStore(SupabaseIngestionStore):
    """rpc 호출 횟수와 페이로드를 기록하는 테스트 대역입니다."""

    def __init__(self, batch_counts: list[int]) -> None:
        super().__init__("https://example.supabase.co", "test-secret")
        self.batch_counts = list(batch_counts)
        self.payloads: list[Any] = []

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        self.payloads.append(kwargs.get("payload"))
        count = self.batch_counts.pop(0) if self.batch_counts else 0
        return [{"source_code": "police_api", "deleted_count": count}]


class RetentionDeleteTests(unittest.TestCase):
    def test_single_short_batch_deletes_once(self) -> None:
        store = RecordingStore([3])

        deleted = store.delete_expired("police_api")

        self.assertEqual(deleted, 3)
        self.assertEqual(len(store.payloads), 1)

    def test_nothing_expired_deletes_zero_in_one_call(self) -> None:
        store = RecordingStore([0])

        deleted = store.delete_expired("police_api")

        self.assertEqual(deleted, 0)
        self.assertEqual(len(store.payloads), 1)

    def test_full_batches_repeat_until_a_short_batch(self) -> None:
        store = RecordingStore([DELETE_BATCH_SIZE, DELETE_BATCH_SIZE, 120])

        deleted = store.delete_expired("police_api")

        self.assertEqual(deleted, DELETE_BATCH_SIZE * 2 + 120)
        self.assertEqual(len(store.payloads), 3)

    def test_each_call_sends_the_batch_limit_and_source(self) -> None:
        store = RecordingStore([DELETE_BATCH_SIZE, 1])

        store.delete_expired("police_api")

        for payload in store.payloads:
            self.assertEqual(payload["p_source_code"], "police_api")
            self.assertEqual(payload["p_limit"], DELETE_BATCH_SIZE)

    def test_backlog_stops_at_the_batch_cap(self) -> None:
        # 300초 함수 예산을 넘기지 않도록 한 실행의 삭제량에 상한을 둔다.
        # 남은 만료분은 다음 실행이 이어서 지운다 — 멱등이므로 안전하다.
        store = RecordingStore([DELETE_BATCH_SIZE] * (DELETE_MAX_BATCHES + 5))

        deleted = store.delete_expired("police_api")

        self.assertEqual(len(store.payloads), DELETE_MAX_BATCHES)
        self.assertEqual(deleted, DELETE_BATCH_SIZE * DELETE_MAX_BATCHES)


if __name__ == "__main__":
    unittest.main()
