"""복구용: 보존기간을 월 단위 구간으로 나눠 기존 _sync_source를 구간별로 호출한다.

저장소 코드는 수정하지 않는다. retention_start만 구간 시작일로 바꿔 끼운다.
각 구간이 끝날 때마다 DB에 upsert되고 ingestion_runs에 한 줄씩 남는다. (멱등 — PRD 3.1-1)
사용: uv run python <이 파일> --source police --start 2026-04-06 --end 2026-10-05
"""
from __future__ import annotations

import argparse
import calendar
import sys
import time
from datetime import date, timedelta

from retriever_lost_found.ingestion import service
from retriever_lost_found.ingestion.store import SupabaseIngestionStore


def month_chunks(start: date, end: date) -> list[tuple[date, date]]:
    chunks, cursor = [], start
    while cursor <= end:
        last_day = date(cursor.year, cursor.month, calendar.monthrange(cursor.year, cursor.month)[1])
        chunk_end = min(last_day, end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=("partner", "police"), default="police")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--newest-first", action="store_true")
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)

    store = SupabaseIngestionStore.from_environment()
    # 최신 구간부터 받으면 latest_registered_on이 뒤 구간의 시작일을 밀어내므로 구간 시작일을 그대로 쓴다.
    store.latest_registered_on = lambda source_code: None
    chunks = month_chunks(start, end)
    if args.newest_first:
        chunks.reverse()
    for chunk_start, chunk_end in chunks:
        # _sync_source: window_start=retention_start(today), window_end=today-1
        service.retention_start = lambda today, months=6, _s=chunk_start: _s
        began = time.monotonic()
        result = service._sync_source(
            store, source=args.source, today=chunk_end + timedelta(days=1)
        )
        print(
            f"{chunk_start}~{chunk_end} status={result['status']} fetched={result['fetched']} "
            f"selected={result['selected']} inserted={result['inserted']} "
            f"updated={result['updated']} unmatched={result['unmatched']} "
            f"{time.monotonic() - began:.0f}s" + (f" error={result.get('error')}" if result.get("error") else ""),
            flush=True,
        )
        if result["status"] != "succeeded":
            print("중단: 구간 실패 — 다음 구간으로 넘어가지 않는다", flush=True)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
