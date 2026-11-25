"""2026-11-23 사용자 결정 — 글 점수 상위 100건으로 자른 뒤, Jev로 찾는 물건과 명백히 다른 물건만 뺀다(PRD 4.5).

- 순서는 바꾸지 않고 빼기만 한다. 뺀 자리를 뒤 순위로 채우지 않는다.
- 사진 첨부 여부와 상관없이 Luna 사진 비교 전에 돈다(search_found_items 안).
- 검색 전체 5초 이내: 남은 시간만 쓰고, 부족하면 건너뛴다. 실패·거절·시간 초과는 남긴다.
- 모델은 typesafe/jev-1.14 하나. 2026-11-23 사용자 결정으로 폴백(baseten/groundhog-1)은 끈다 — 보정 50케이스에서
  임계값 0.99에서도 정답을 뺐다. Jev가 실패하면 아무것도 빼지 않는다.
- 물품 글은 mask_item_row로 가린 뒤 보낸다(AGENTS.md 4장 — LLM 입력 마스킹).
- 2026-11-23 비용 최적화(사용자 승인): 설명의 자동 문구는 보내지 않는다. 다음 쪽은 첫 쪽 판정 결과(커서)를 다시 쓴다.
- 2026-11-25 사용자 결정 — 상위 100건을 모두 판정한다. 25건씩 구간으로 나눠 구간마다 요청 하나를 동시에 보내고
  (물품이 30건이면 2요청, 73건이면 3요청), 구간별 임계값을 쓴다: 1~25위 0.92, 26~100위 0.87.
  을지로3가 1km 20케이스·2회 평균 오탐 제거 21건(상위 30·0.92) → 560건(docs/measurements.md).
  알림 판정(alerts/judge.py)도 같은 설정을 쓴다(2026-11-25 사용자 결정).
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, wait
from contextvars import copy_context
from time import monotonic
from typing import Any

from ..privacy.masking import mask_item_row

MODEL = "typesafe/jev-1.14"
# "예(명백히 다른 물건)" 확률이 이 값 이상이면 뺀다. 2026-11-23 보정 50케이스에서 정답을 하나도 빼지 않는
# 최저값 0.87 + 재요청 흔들림(±0.08) 여유 0.05(docs/measurements.md). 같은 날 최적화한 입력 형식(상위 30건)으로
# 다시 보정해도 0.87 → 0.92로 같았다. 모델·입력 형식을 바꾸면 다시 정한다.
THRESHOLD = 0.92
# 2026-11-25 사용자 결정 — 순위 25건 구간마다 요청 하나, 구간별 임계값. 정답 확률은 순위와 무관하다고 보고
# 구간을 나누지 않은 정답 확률로 정했다. 0.87은 쇼핑백 묶음 정답 3건(설명 없이 판정, 사용자가 감수하기로 함)
# 말고는 정답을 하나도 빼지 않는 값이다 — 0.85부터 '루이비통 지갑'의 카드지갑 정답이 빠졌다(docs/measurements.md).
SEGMENT_SIZE = 25
SEGMENT_THRESHOLDS = (THRESHOLD, 0.87, 0.87, 0.87)
TOP_N = SEGMENT_SIZE * len(SEGMENT_THRESHOLDS)  # 100 — 검색 결과 상한(hybrid.SEARCH_RESULT_LIMIT)과 같다
MAX_REQUEST_BYTES = 32_000      # 이보다 긴 글은 느리고 덜 정확한 long-context 모드로 간다(OpenRouter Jev 문서).
MIN_BUDGET_SECONDS = 0.6        # 남은 시간이 이보다 짧으면 부르지 않는다.
NAME_CHARS, DESCRIPTION_CHARS = 80, 120
RULE = ("각 질문의 습득물이 찾는 분실물과 명백히 다른 물건이면 예. 같은 물건일 수 있거나 정보가 부족하거나 "
        "애매하면 아니오. 같은 종류 안의 다른 물건(예: 카드지갑을 찾는데 장지갑)은 예. 색 차이만으로는 예라고 하지 않는다. "
        "분류는 등록할 때 잘못 들어가는 일이 있으니, 분류와 물품명이 어긋나면 물품명을 따른다.")
QUESTION = "{key}는 찾는 분실물과 명백히 다른 물건인가?"  # 키를 꼭 넣는다 — 빼면 어느 물품 질문인지 몰라 판별력이 떨어졌다(AUC 0.94→0.76)
# 수집 원문의 자동 설명: "{물품명}({팔레트}색)을 습득하여 보관하고 있습니다." — 물품명·색을 되풀이할 뿐이다.
_BOILERPLATE = re.compile(r"\s*\((?:[^()]*\([^()]*\)|[^()]*)색\)\s*|\s*을\s*습득하여\s*보관하고\s*있습니다\.?\s*$")
_PALETTE = re.compile(r"^[^()]*\(([^()]+)\)$")  # "블랙(검정)" → "검정"
# 검색 동시 4개(hybrid._search_slots) × 구간 4개 요청이 기다리지 않게 16개.
_pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="jev")


def _description(name: str, description: str) -> str:
    rest = _BOILERPLATE.sub("", description).strip()
    return "" if not rest or rest == name.strip() else rest[:DESCRIPTION_CHARS]


def _describe(row: dict[str, Any]) -> str:
    masked = mask_item_row(row)
    name = str(masked.get("item_name") or "")
    color = str(masked.get("color_name") or "")
    color = (_PALETTE.match(color) or [None, color])[1]
    parts = [name[:NAME_CHARS], str(masked.get("raw_category_name") or ""), color,
             _description(name, str(masked.get("description") or ""))]
    return " / ".join(part for part in parts if part)


def build_request(query: str, rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, int]]:
    """(state, questions, 질문 키 → 물품 id). 키는 짧게(i1, i2 …) — 물품 id는 서버 밖으로 보내지 않는다."""
    keys = {f"i{n}": int(row["id"]) for n, row in enumerate(rows, 1)}
    state = {"찾는 분실물": query, "판단 규칙": RULE, "습득물": dict(zip(keys, map(_describe, rows)))}
    questions = {key: {"type": "noul", "instructions": QUESTION.format(key=key)} for key in keys}
    return state, questions, keys


class JevFilter:
    def __init__(self, client, *, segment_size: int = SEGMENT_SIZE,
                 thresholds: tuple[float, ...] = SEGMENT_THRESHOLDS):
        """구간 하나가 요청 하나다. thresholds[k]가 k번째 구간(순위 k·segment_size+1위부터)의 임계값이고,
        판정 범위는 segment_size × len(thresholds)건이다."""
        self.client = client
        self.segment_size = segment_size
        self.thresholds = thresholds

    def _judge(self, query: str, chunk: list[dict[str, Any]], threshold: float, deadline: float,
               budget: float) -> tuple[set[int], str]:
        """구간 하나에서 뺄 id와 상태(ok/unavailable)."""
        state, questions, keys = build_request(query, chunk)
        remaining = min(budget, deadline - monotonic())  # 부동소수 오차로 예산을 넘지 않게
        if remaining < 0.05:
            return set(), "unavailable"
        try:
            answers = self.client.decide(MODEL, state, questions, timeout=remaining)
        except RuntimeError:
            return set(), "unavailable"
        return {keys[key] for key, p in answers.items() if key in keys and p is not None and p >= threshold}, "ok"

    def apply(self, query: str, rows: list[dict[str, Any]], *,
              budget: float) -> tuple[list[dict[str, Any]], str, set[int]]:
        """(남길 물품, 상태, 뺀 id). 물품이 있는 구간만 부른다 — 30건이면 2요청, 73건이면 3요청."""
        if not rows:
            return rows, "empty", set()
        if budget < MIN_BUDGET_SECONDS:
            return rows, "skipped", set()
        deadline = monotonic() + budget
        size = self.segment_size
        judged = rows[:size * len(self.thresholds)]
        # 구간은 Jev 전 순위로 나눈다(색 필터 뒤 100건이 안 돼도 1~25위가 1구간, 26위부터 2구간).
        chunks = [(judged[i:i + size], self.thresholds[i // size]) for i in range(0, len(judged), size)]
        futures = [_pool.submit(copy_context().run, self._judge, query, chunk, threshold, deadline, budget)
                   for chunk, threshold in chunks]
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
        return [row for row in rows if int(row["id"]) not in excluded], status, excluded
