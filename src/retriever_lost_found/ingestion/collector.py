from __future__ import annotations

import argparse
import calendar
import csv
import json
import sys
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from .categories import (
    ServiceCategory,
    classify_product_category,
    normalize_text,
)
from ..integrations.partner_api import create_client as create_partner_client
from ..integrations.police_api import PoliceFoundItemsClient, read_service_key


CSV_FIELDS = (
    "item_source_code",
    "atc_id",
    "found_sequence",
    "service_category_code",
    "service_category_name",
    "raw_category_name",
    "color_name",
    "item_name",
    "description",
    "image_url",
    "raw_storage_name",
    "registered_on",
    "normalized_search_text",
    "raw_payload",
)

# PRD 3.1-2, 4.1 — 완료된 어제까지 달력 기준 6개월을 보존합니다.
SOURCE_RETENTION_MONTHS = {"partner": 6, "police": 6}
SOURCE_CODES = {"partner": "partner_api", "police": "police_api"}
KOREA_TIMEZONE = timezone(timedelta(hours=9))


def retention_start(today: date, months: int = 6) -> date:
    """오늘을 제외한 최근 달력상 months개월의 첫날을 반환합니다."""
    month_index = today.year * 12 + today.month - 1 - months
    year, month_zero_based = divmod(month_index, 12)
    month = month_zero_based + 1
    day = min(today.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


class PageClient(Protocol):
    def fetch_page(
        self,
        page_no: int,
        num_of_rows: int = 100,
        **filters: str,
    ) -> tuple[list[dict[str, str]], int]: ...


def create_source_client(source: str) -> PageClient:
    if source == "partner":
        return create_partner_client()
    if source == "police":
        return PoliceFoundItemsClient(read_service_key())
    raise ValueError(f"지원하지 않는 출처입니다: {source}")


def parse_api_date(value: str) -> date | None:
    normalized = normalize_text(value)
    for format_string in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(normalized, format_string).date()
        except ValueError:
            continue
    return None


def build_search_text(item: dict[str, Any]) -> str:
    values = (
        item.get("fdPrdtNm"),
        item.get("fdSbjt"),
        item.get("clrNm"),
        item.get("prdtClNm"),
    )
    return normalize_text(" ".join(str(value or "") for value in values))


def fetch_page_with_retry(
    client: PageClient,
    page_no: int,
    rows_per_page: int,
    filters: dict[str, str],
    *,
    attempts: int = 5,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[list[dict[str, str]], int]:
    delay = 1.0
    for attempt in range(1, attempts + 1):
        try:
            return client.fetch_page(page_no, rows_per_page, **filters)
        except (RuntimeError, TimeoutError, ValueError) as error:
            if attempt == attempts:
                raise
            print(
                f"페이지 {page_no} 실패({error}), {delay:g}초 후 재시도 "
                f"({attempt}/{attempts - 1})",
                file=sys.stderr,
                flush=True,
            )
            sleep(delay)
            delay = min(delay * 2, 16)
    raise AssertionError("도달할 수 없는 코드")


def to_csv_row(
    item: dict[str, str],
    category: ServiceCategory,
    item_source_code: str,
    registered_on: date,
) -> dict[str, str]:
    return {
        "item_source_code": item_source_code,
        "atc_id": normalize_text(item.get("atcId")),
        "found_sequence": normalize_text(item.get("fdSn")) or "1",
        "service_category_code": category.code,
        "service_category_name": category.name,
        "raw_category_name": normalize_text(item.get("prdtClNm")),
        "color_name": normalize_text(item.get("clrNm")),
        "item_name": normalize_text(item.get("fdPrdtNm")),
        "description": normalize_text(item.get("fdSbjt")),
        "image_url": normalize_text(item.get("fdFilePathImg")),
        "raw_storage_name": normalize_text(item.get("depPlace")),
        "registered_on": registered_on.isoformat(),
        "normalized_search_text": build_search_text(item),
        "raw_payload": json.dumps(item, ensure_ascii=False, separators=(",", ":")),
    }


def collect_selected_rows(
    client: PageClient,
    *,
    source: str,
    start_date: date,
    end_date: date,
    rows_per_page: int = 100,
) -> tuple[list[dict[str, str]], dict[str, int]]:
    if start_date > end_date:
        raise ValueError("시작일은 종료일보다 늦을 수 없습니다.")
    if source not in SOURCE_CODES:
        raise ValueError(f"지원하지 않는 출처입니다: {source}")
    if not 1 <= rows_per_page <= 100:
        raise ValueError("rows_per_page는 1~100 범위여야 합니다.")

    filters = {
        "START_YMD": start_date.strftime("%Y%m%d"),
        "END_YMD": end_date.strftime("%Y%m%d"),
    }
    unique_rows: dict[tuple[str, str], dict[str, str]] = {}
    fetched_count = 0
    invalid_count = 0
    outside_range_count = 0
    page_no = 1
    total_count = 0

    while True:
        page_items, total_count = fetch_page_with_retry(
            client,
            page_no,
            rows_per_page,
            filters,
        )
        if not page_items:
            break

        fetched_count += len(page_items)
        for item in page_items:
            atc_id = normalize_text(item.get("atcId"))
            found_sequence = normalize_text(item.get("fdSn")) or "1"
            registered_on = parse_api_date(item.get("fdYmd", ""))
            if not atc_id or registered_on is None:
                invalid_count += 1
                continue
            if not start_date <= registered_on <= end_date:
                outside_range_count += 1
                continue

            category = classify_product_category(item.get("prdtClNm"))
            if category is None:
                continue

            key = (atc_id, found_sequence)
            unique_rows[key] = to_csv_row(
                item,
                category,
                SOURCE_CODES[source],
                registered_on,
            )

        if page_no % 10 == 0 or page_no * rows_per_page >= total_count:
            print(
                f"{source}: {min(page_no * rows_per_page, total_count):,}/"
                f"{total_count:,}건 확인, {len(unique_rows):,}건 선별",
                file=sys.stderr,
                flush=True,
            )

        if page_no * rows_per_page >= total_count:
            break
        page_no += 1

    rows = sorted(
        unique_rows.values(),
        key=lambda row: (
            row["registered_on"],
            row["atc_id"],
            row["found_sequence"],
        ),
        reverse=True,
    )
    return rows, {
        "api_total_count": total_count,
        "fetched_count": fetched_count,
        "selected_count": len(rows),
        "invalid_count": invalid_count,
        "outside_range_count": outside_range_count,
    }


def write_csv(rows: list[dict[str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary_path.replace(output_path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="경찰청 API에서 지정 기간의 6개 서비스 카테고리만 CSV로 저장합니다."
    )
    parser.add_argument("--source", choices=sorted(SOURCE_CODES), required=True)
    parser.add_argument("--start", help="조회 시작일 YYYY-MM-DD")
    parser.add_argument("--end", help="조회 종료일 YYYY-MM-DD (기본: 어제)")
    parser.add_argument("--rows", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    end_date = (
        date.fromisoformat(args.end)
        if args.end
        else datetime.now(KOREA_TIMEZONE).date() - timedelta(days=1)
    )
    start_date = (
        date.fromisoformat(args.start)
        if args.start
        else retention_start(
            end_date + timedelta(days=1), SOURCE_RETENTION_MONTHS[args.source]
        )
    )

    client = create_source_client(args.source)
    rows, summary = collect_selected_rows(
        client,
        source=args.source,
        start_date=start_date,
        end_date=end_date,
        rows_per_page=args.rows,
    )
    write_csv(rows, args.output)
    print(
        json.dumps(
            {
                "source": args.source,
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
                "output": str(args.output.resolve()),
                **summary,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
