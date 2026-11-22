"""2026-11-23 사용자 결정 — 글 점수 상위 100건으로 자른 뒤, Jev로 찾는 물건과 명백히 다른 물건만 뺀다(PRD 4.5).

- 순서는 바꾸지 않고 빼기만 한다. 뺀 자리를 101위 이후로 채우지 않는다.
- 사진 첨부 여부와 상관없이 Luna 사진 비교 전에 돈다(search_found_items 안).
- 검색 전체 5초 이내: 남은 시간만 쓰고, 부족하면 건너뛴다. 실패·거절·시간 초과는 남긴다.
- 모델은 typesafe/jev-1.14 하나. 2026-11-23 사용자 결정으로 폴백(baseten/groundhog-1)은 끈다 — 보정 50케이스에서
  임계값 0.99에서도 정답을 뺐다. Jev가 실패하면 아무것도 빼지 않는다.
- 물품 글은 mask_item_row로 가린 뒤 보낸다(AGENTS.md 4장 — LLM 입력 마스킹).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from contextvars import copy_context
from time import monotonic
from typing import Any

from ..privacy.masking import mask_item_row

MODEL = "typesafe/jev-1.14"
# "예(명백히 다른 물건)" 확률이 이 값 이상이면 뺀다. 2026-11-23 보정 50케이스에서 정답을 하나도 빼지 않는
# 최저값 0.87 + 재요청 흔들림(±0.08) 여유 0.05(docs/measurements.md). 모델을 바꾸면 다시 정한다.
THRESHOLD = 0.92
CHUNK_SIZE = 25                 # 묶음 하나의 물품 수. 묶음들은 병렬로 보낸다.
MAX_REQUEST_BYTES = 32_000      # 이보다 긴 글은 느리고 덜 정확한 long-context 모드로 간다(OpenRouter Jev 문서).
MIN_BUDGET_SECONDS = 0.6        # 남은 시간이 이보다 짧으면 부르지 않는다.
NAME_CHARS, DESCRIPTION_CHARS = 80, 120
RULE = ("각 질문의 습득물이 찾는 분실물과 명백히 다른 물건이면 예. 같은 물건일 수 있거나 정보가 부족하거나 "
        "애매하면 아니오. 같은 종류 안의 다른 물건(예: 카드지갑을 찾는데 장지갑)은 예. 색 차이만으로는 예라고 하지 않는다.")
_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="jev")


def _describe(row: dict[str, Any]) -> str:
    masked = mask_item_row(row)
    parts = [str(masked.get("item_name") or "")[:NAME_CHARS], str(masked.get("raw_category_name") or ""),
             str(masked.get("color_name") or ""), str(masked.get("description") or "")[:DESCRIPTION_CHARS]]
    return " / ".join(part for part in parts if part)


def build_request(query: str, rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    keys = [f"item_{int(row['id'])}" for row in rows]
    state = {"찾는 분실물": query, "판단 규칙": RULE, "습득물": dict(zip(keys, map(_describe, rows)))}
    questions = {key: {"type": "noul", "instructions": f"{key}는 찾는 분실물과 명백히 다른 물건인가?"} for key in keys}
    return state, questions


class JevFilter:
    def __init__(self, client):
        self.client = client

    def _judge(self, query: str, chunk: list[dict[str, Any]], deadline: float,
               budget: float) -> tuple[set[int], str]:
        """묶음 하나에서 뺄 id와 상태(ok/unavailable)."""
        state, questions = build_request(query, chunk)
        remaining = min(budget, deadline - monotonic())  # 부동소수 오차로 예산을 넘지 않게
        if remaining < 0.05:
            return set(), "unavailable"
        try:
            answers = self.client.decide(MODEL, state, questions, timeout=remaining)
        except RuntimeError:
            return set(), "unavailable"
        return {int(key.split("_", 1)[1]) for key, p in answers.items() if p is not None and p >= THRESHOLD}, "ok"

    def apply(self, query: str, rows: list[dict[str, Any]], *, budget: float) -> tuple[list[dict[str, Any]], str]:
        if not rows:
            return rows, "empty"
        if budget < MIN_BUDGET_SECONDS:
            return rows, "skipped"
        deadline = monotonic() + budget
        chunks = [rows[i:i + CHUNK_SIZE] for i in range(0, len(rows), CHUNK_SIZE)]
        futures = [_pool.submit(copy_context().run, self._judge, query, chunk, deadline, budget) for chunk in chunks]
        done, _ = wait(futures, timeout=max(0.0, deadline - monotonic()) + 0.05)
        excluded: set[int] = set()
        statuses = set()
        for future in futures:
            if future in done and future.exception() is None:
                ids, status = future.result()
                excluded |= ids
                statuses.add(status)
            else:
                statuses.add("unavailable")
        status = "unavailable" if "unavailable" in statuses else "ok"
        return [row for row in rows if int(row["id"]) not in excluded], status
