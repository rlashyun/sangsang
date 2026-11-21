"""마스킹 전 원문으로 만든 물품 벡터를 다시 만드는 수동 배치 — PRD 3.3 · Q5.

PR #74 전에는 임베딩 API로 원문(또는 괄호 이름을 가리지 않은 사본)을 보냈다. 그 벡터 중
**지금 규칙으로 가리면 입력이 달라지는 것만** 가린 입력으로 다시 만든다.

- 기본은 dry-run이다. `--apply`를 줘야 외부 임베딩 API를 부르고 DB에 쓴다(비용·국외 전송, 사용자 승인 후).
- `--embedded-before`(필수)보다 먼저 만든 벡터만 본다. 마스킹 전송(PR #74) 운영 배포 시각을 넣는다.
  바꾼 행은 embedded_at이 새 시각이 되어 다시 실행해도 대상이 되지 않는다.
- 외부 API에는 `embedding/service.py`와 같은 규칙으로 가린 사본만 보낸다. 분류를 모르는 물품은 고위험으로 본다.
- `found_items` 원문·`embedded_input` 스냅숏은 바꾸지 않는다(절대 규칙 3). 출력에는 건수와 마지막 id만 남긴다.

실행 예: `uv run python -m retriever_lost_found.privacy.reembed --embedded-before 2026-11-20T18:50:00+09:00`
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from ..config import DEFAULT_ENV_FILE, read_optional_env_value
from ..embedding.store import EmbeddingStore
from ..ingestion.store import SupabaseRequestError
from .masking import is_high_risk_category, mask_text

PAGE_SIZE = 1000
EMBED_BATCH = 32  # embed_many·RPC 상한


def masked_input(embedded_input: str, raw_category_name: str | None, *, known: bool) -> str:
    # embedding/service.py와 같은 판정 — 분류를 읽지 못한 물품은 더 엄격한(고위험) 규칙으로 가린다.
    high_risk = not known or is_high_risk_category(raw_category_name)
    return mask_text(embedded_input, high_risk=high_risk) or ""


def plan_candidates(rows: list[dict[str, Any]], categories: dict[int, str | None]) -> list[dict[str, Any]]:
    """가리면 입력이 달라지는 벡터만 고른다. 원문과 같으면 이미 안전하므로 다시 만들지 않는다."""
    candidates = []
    for row in rows:
        item_id = int(row["item_id"])
        text = row.get("embedded_input")
        if not isinstance(text, str) or not text.strip():
            continue
        masked = masked_input(text, categories.get(item_id), known=item_id in categories)
        if masked != text and masked.strip():
            candidates.append({**row, "item_id": item_id, "masked_input": masked})
    return candidates


class ReembedStore(Protocol):
    def fetch_page(self, after_id: int, limit: int, embedded_before: str) -> list[dict[str, Any]]: ...

    def categories(self, item_ids: list[int]) -> dict[int, str | None]: ...

    def replace(self, records: list[dict[str, Any]], embedded_before: str) -> int: ...


class EmbeddingClient(Protocol):
    def embed_many(self, texts: list[str], *, timeout: float = 30) -> list[list[float]]: ...


class SupabaseReembedStore(EmbeddingStore):
    """service_role 전용(절대 규칙 6). item_id 오름차순 키셋 페이징으로 읽는다."""

    def fetch_page(self, after_id: int, limit: int, embedded_before: str) -> list[dict[str, Any]]:
        try:
            rows = self._request("GET", "found_item_embeddings_bge_m3", query=[
                ("select", "item_id,embedded_input,content_hash"),
                ("item_id", f"gt.{after_id}"),
                ("is_current", "is.true"),
                ("input_version", "eq.color_ko_item_name:v2"),
                ("embedded_at", f"lt.{embedded_before}"),
                ("order", "item_id.asc"),
                ("limit", str(limit)),
            ])
        except SupabaseRequestError:
            raise RuntimeError("임베딩 재생성 DB 읽기 실패") from None
        if not isinstance(rows, list):
            raise RuntimeError("임베딩 응답 형식이 올바르지 않습니다.")
        return rows

    def replace(self, records: list[dict[str, Any]], embedded_before: str) -> int:
        result = self.rpc("replace_masked_bge_m3_embeddings", {
            "records": records, "p_embedded_before": embedded_before})
        return int(result or 0)


def run_reembed(
    store: ReembedStore,
    client: EmbeddingClient | None,
    *,
    embedded_before: str,
    apply: bool,
    page_size: int = PAGE_SIZE,
    start_after_id: int = 0,
    max_pages: int | None = None,
    report: Callable[[dict[str, Any]], None] = lambda _: None,
) -> dict[str, Any]:
    if not 1 <= page_size <= PAGE_SIZE:
        raise ValueError(f"page_size는 1~{PAGE_SIZE} 범위여야 합니다.")
    datetime.fromisoformat(embedded_before)  # 형식 오류는 DB에 닿기 전에 ValueError로 막는다
    if apply and client is None:
        raise ValueError("--apply에는 임베딩 API 키가 필요합니다.")
    summary = {"mode": "apply" if apply else "dry_run", "scanned": 0, "candidates": 0,
               "replaced": 0, "skipped_changed": 0, "last_id": start_after_id, "complete": False}
    after_id, pages = start_after_id, 0
    while max_pages is None or pages < max_pages:
        rows = store.fetch_page(after_id, page_size, embedded_before)
        if not rows:
            summary["complete"] = True
            break
        pages += 1
        ids = [int(row["item_id"]) for row in rows]
        candidates = plan_candidates(rows, store.categories(ids))
        summary["scanned"] += len(rows)
        summary["candidates"] += len(candidates)
        if apply:
            for start in range(0, len(candidates), EMBED_BATCH):
                batch = candidates[start:start + EMBED_BATCH]
                vectors = client.embed_many([row["masked_input"] for row in batch])
                records = [
                    {"item_id": row["item_id"], "content_hash": row["content_hash"],
                     "embedded_input": row["embedded_input"],
                     "embedding": json.dumps(vector, separators=(",", ":"))}
                    for row, vector in zip(batch, vectors, strict=True)
                ]
                replaced = store.replace(records, embedded_before)
                summary["replaced"] += replaced
                # 읽은 뒤 물품이 바뀌었거나 다른 작업이 먼저 바꾼 행은 DB가 건너뛴다.
                summary["skipped_changed"] += len(batch) - replaced
        after_id = max(ids)
        summary["last_id"] = after_id
        report(dict(summary))
        if len(rows) < page_size:
            summary["complete"] = True
            break
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="마스킹 전 원문 기반 물품 벡터 재생성(기본 dry-run)")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--embedded-before", required=True,
                        help="이 시각보다 먼저 만든 벡터만 대상(ISO 8601, 시간대 포함). 마스킹 전송 배포 시각")
    parser.add_argument("--apply", action="store_true", help="외부 임베딩 API를 부르고 DB에 씁니다.")
    parser.add_argument("--page-size", type=int, default=PAGE_SIZE)
    parser.add_argument("--start-after-id", type=int, default=0, help="중단한 지점(last_id)부터 이어서 실행")
    parser.add_argument("--max-pages", type=int)
    args = parser.parse_args(argv)
    if datetime.fromisoformat(args.embedded_before).tzinfo is None:
        parser.error("--embedded-before에는 시간대를 넣어야 합니다(예: +09:00).")

    def configured(name: str) -> str:
        return read_optional_env_value(name, args.env_file)

    store = SupabaseReembedStore(
        configured("SUPABASE_URL"),
        configured("SUPABASE_SECRET_KEY") or configured("SUPABASE_SERVICE_ROLE_KEY"),
    )
    client = None
    if args.apply:
        from ..integrations.embeddings import BgeM3Client

        key = configured("OPENROUTER_API_KEY") or configured("openrouter_key")
        client = BgeM3Client(key) if key else None
    summary = run_reembed(
        store, client, embedded_before=args.embedded_before, apply=args.apply,
        page_size=args.page_size, start_after_id=args.start_after_id, max_pages=args.max_pages,
        report=lambda value: print(json.dumps(value, ensure_ascii=False), file=sys.stderr, flush=True),
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as error:
        # 공급자 URL·응답 본문·비밀값은 출력하지 않는다.
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}), flush=True)
        raise SystemExit(1) from None
