from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .errors import safe_error
from .store import AlertStore


class SimilarityJudge(Protocol):
    def __call__(
        self, query: str, candidates: list[dict[str, Any]]
    ) -> list[tuple[dict[str, Any], float]]: ...


# 설계 3.2 — ⑥(검색 방식 확정) 결론으로 채운다(Task 11). None이면 매칭 단계를 돌리지 않는다.
SIMILARITY_JUDGE: SimilarityJudge | None = None


@dataclass(frozen=True)
class MatchResult:
    items: list[tuple[dict[str, Any], float]]
    checked_until: str | None


def find_new_similar_items(
    store: AlertStore,
    judge: SimilarityJudge,
    reservation: dict[str, Any],
    item_source_code: str,
) -> MatchResult:
    """설계 3.2 — ⑥과의 경계. 판정을 DB로 옮기게 되면 이 함수의 속만 바꾼다."""
    payload = store.candidate_items(int(reservation["id"]), item_source_code)
    candidates = payload.get("items") or []
    matched = judge(str(reservation["query"]), candidates) if candidates else []
    return MatchResult(items=list(matched), checked_until=payload.get("checked_until"))


def run_match_pass(
    store: AlertStore,
    judge: SimilarityJudge,
    *,
    item_source_code: str,
    deadline: float,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """설계 6.1 — 수집 크론 끝에서 이 출처의 새 물품만 활성 예약과 비교한다."""
    run_id = store.start_run("match", item_source_code)
    counts = {"reservations_checked": 0, "reservations_matched": 0, "items_queued": 0}
    status = "succeeded"
    error_message = None
    try:
        for reservation in store.match_queue(item_source_code):
            if clock() >= deadline:
                # 남은 예약은 커서가 오래된 순으로 다음 날 먼저 처리된다.
                status = "partial"
                break
            result = find_new_similar_items(store, judge, reservation, item_source_code)
            recorded = store.record_match(
                int(reservation["id"]),
                item_source_code,
                result.checked_until,
                [(int(item["id"]), float(score)) for item, score in result.items],
            )
            counts["reservations_checked"] += 1
            queued = int(recorded.get("items_queued") or 0)
            if queued:
                counts["reservations_matched"] += 1
                counts["items_queued"] += queued
    except Exception as error:  # 설계 6.1 — 실패는 alert_runs에만 남긴다
        status = "failed"
        error_message = safe_error(error, (store.secret_key,))
    store.finish_run(run_id, {"status": status, **counts, "error_message": error_message})
    return {"status": status, **counts, "error_message": error_message}
