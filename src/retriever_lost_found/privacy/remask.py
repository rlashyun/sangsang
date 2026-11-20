"""기존 행의 `normalized_search_text`를 마스킹 규칙에 맞추는 수동 배치 — PRD 3.3 · Q5.

새로 수집하는 행은 `ingestion.collector.build_search_text`가 이미 가린다. 이 도구는 그 전에 저장된
행 중 **마스킹 결과가 달라지는 행만** 고친다.

- 기본은 dry-run이다. `--apply`를 줘야 DB에 쓴다.
- 바꾸는 칸은 정규화 전용 `normalized_search_text` 하나다. `item_name`·`description`·`raw_payload`
  원문은 읽지도 쓰지도 않는다(절대 규칙 3).
- 출력에는 건수와 마지막 id만 남긴다. 원문·마스킹 결과 텍스트는 찍지 않는다(개인정보 재노출 방지).
- 운영 실행 순서와 영향(검색 문서 트리거 재계산 등)은 docs/backlog.md를 따른다.

실행 예: `uv run python -m retriever_lost_found.privacy.remask` (dry-run)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Protocol

from ..config import DEFAULT_ENV_FILE, read_optional_env_value
from ..integrations.supabase_rest import SupabaseRestClient
from .masking import is_high_risk_category, mask_text

PAGE_SIZE = 1000


def remasked_search_text(row: dict[str, Any]) -> str | None:
    """마스킹 후 값이 달라지면 새 값을, 같으면 None을 돌려줍니다."""
    text = row.get("normalized_search_text")
    if not isinstance(text, str) or not text:
        return None
    masked = mask_text(text, high_risk=is_high_risk_category(row.get("raw_category_name")))
    return masked if masked != text else None


def plan_updates(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    updates = []
    for row in rows:
        masked = remasked_search_text(row)
        if masked is not None:
            updates.append({"id": int(row["id"]), "normalized_search_text": masked})
    return updates


class RemaskStore(Protocol):
    def fetch_page(self, after_id: int, limit: int) -> list[dict[str, Any]]: ...

    def update_search_text(self, item_id: int, text: str) -> None: ...


class SupabaseRemaskStore(SupabaseRestClient):
    """service_role 전용(절대 규칙 6). id 오름차순 키셋 페이징으로 읽는다."""

    error_label = "Supabase 재마스킹"

    def __init__(self, url: str, secret_key: str, *, timeout: float = 30.0) -> None:
        if not url or not secret_key:
            raise ValueError("Supabase URL과 서버 전용 secret key가 필요합니다.")
        self.url = url.rstrip("/")
        self.secret_key = secret_key
        self.timeout = timeout
        self.deadline = None

    def fetch_page(self, after_id: int, limit: int) -> list[dict[str, Any]]:
        rows = self._request(
            "GET",
            "found_items",
            query=[
                ("select", "id,raw_category_name,normalized_search_text"),
                ("id", f"gt.{after_id}"),
                ("order", "id.asc"),
                ("limit", str(limit)),
            ],
        )
        if not isinstance(rows, list):
            raise RuntimeError("found_items 응답 형식이 올바르지 않습니다.")
        return rows

    def update_search_text(self, item_id: int, text: str) -> None:
        self._request(
            "PATCH",
            "found_items",
            query=[("id", f"eq.{item_id}")],
            payload={"normalized_search_text": text},
            prefer="return=minimal",
        )


def run_remask(
    store: RemaskStore,
    *,
    apply: bool,
    page_size: int = PAGE_SIZE,
    start_after_id: int = 0,
    max_pages: int | None = None,
    report: Callable[[dict[str, Any]], None] = lambda _: None,
) -> dict[str, Any]:
    if not 1 <= page_size <= PAGE_SIZE:
        raise ValueError(f"page_size는 1~{PAGE_SIZE} 범위여야 합니다.")
    summary = {"mode": "apply" if apply else "dry_run", "scanned": 0, "changed": 0, "updated": 0,
               "last_id": start_after_id, "complete": False}
    after_id, pages = start_after_id, 0
    while max_pages is None or pages < max_pages:
        rows = store.fetch_page(after_id, page_size)
        if not rows:
            summary["complete"] = True
            break
        pages += 1
        updates = plan_updates(rows)
        summary["scanned"] += len(rows)
        summary["changed"] += len(updates)
        if apply:
            for update in updates:
                store.update_search_text(update["id"], update["normalized_search_text"])
                summary["updated"] += 1
        after_id = max(int(row["id"]) for row in rows)
        summary["last_id"] = after_id
        report(dict(summary))
        if len(rows) < page_size:
            summary["complete"] = True
            break
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="기존 normalized_search_text 개인정보 재마스킹(기본 dry-run)")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--apply", action="store_true", help="실제로 DB에 씁니다. 없으면 건수만 셉니다.")
    parser.add_argument("--page-size", type=int, default=PAGE_SIZE)
    parser.add_argument("--start-after-id", type=int, default=0, help="중단한 지점(last_id)부터 이어서 실행")
    parser.add_argument("--max-pages", type=int)
    args = parser.parse_args(argv)

    url = read_optional_env_value("SUPABASE_URL", args.env_file)
    secret = read_optional_env_value("SUPABASE_SECRET_KEY", args.env_file) or read_optional_env_value(
        "SUPABASE_SERVICE_ROLE_KEY", args.env_file
    )
    store = SupabaseRemaskStore(url, secret)
    summary = run_remask(
        store, apply=args.apply, page_size=args.page_size, start_after_id=args.start_after_id,
        max_pages=args.max_pages,
        report=lambda value: print(json.dumps(value, ensure_ascii=False), file=sys.stderr, flush=True),
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
