from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Any

from .collector import (
    KOREA_TIMEZONE,
    SOURCE_CODES,
    SOURCE_RETENTION_MONTHS,
    collect_selected_rows,
    create_source_client,
    retention_start,
)
from .store import LocationReference, RunAlreadyActive, SupabaseIngestionStore


SOURCE_ORDER = ("partner", "police")


def _normalize_key(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _base_name(value: Any) -> str:
    without_notes = re.sub(r"\([^)]*\)|\[[^]]*\]", "", str(value or ""))
    return _normalize_key(without_notes)


def _resolve_location(
    raw_storage_name: str,
    aliases: dict[str, int],
    locations: list[LocationReference],
) -> tuple[int | None, str]:
    normalized = _normalize_key(raw_storage_name)
    if not normalized:
        return None, "unmatched"
    if normalized in aliases:
        return aliases[normalized], "matched"

    exact_candidates = {
        location.id
        for location in locations
        if normalized
        in {
            _normalize_key(location.normalized_name),
            _normalize_key(location.name),
            _base_name(location.name),
        }
    }
    if len(exact_candidates) == 1:
        return next(iter(exact_candidates)), "matched"
    if len(exact_candidates) > 1:
        return None, "ambiguous"

    suffix_candidates = {
        location.id
        for location in locations
        if _normalize_key(location.normalized_name).endswith(normalized)
        or normalized.endswith(_normalize_key(location.normalized_name))
    }
    if len(suffix_candidates) == 1:
        return next(iter(suffix_candidates)), "matched"
    if suffix_candidates:
        return None, "ambiguous"
    return None, "unmatched"


def _build_database_rows(
    selected_rows: list[dict[str, str]],
    *,
    source_code: str,
    category_ids: dict[str, int],
    aliases: dict[str, int],
    locations: list[LocationReference],
) -> tuple[list[dict[str, Any]], int]:
    database_rows: list[dict[str, Any]] = []
    unmatched_count = 0
    for row in selected_rows:
        normalized_category = _normalize_key(row["raw_category_name"])
        category_id = category_ids.get(normalized_category)
        if category_id is None:
            raise RuntimeError(
                f"DB 카테고리 매핑이 없습니다: {source_code}/{row['raw_category_name']}"
            )
        location_id, match_status = _resolve_location(
            row["raw_storage_name"], aliases, locations
        )
        if match_status != "matched":
            unmatched_count += 1
        try:
            raw_payload = json.loads(row["raw_payload"])
        except json.JSONDecodeError as error:
            raise RuntimeError("API 원본 데이터 JSON 변환에 실패했습니다.") from error
        database_rows.append(
            {
                "item_source_code": source_code,
                "atc_id": row["atc_id"],
                "found_sequence": row["found_sequence"],
                "storage_location_id": location_id,
                "location_match_status": match_status,
                "category_id": category_id,
                "raw_category_name": row["raw_category_name"],
                "color_name": row["color_name"] or None,
                "item_name": row["item_name"],
                "description": row["description"],
                "image_url": row["image_url"] or None,
                "raw_storage_name": row["raw_storage_name"],
                "registered_on": row["registered_on"],
                "normalized_search_text": row["normalized_search_text"],
                "raw_payload": raw_payload,
            }
        )
    return database_rows, unmatched_count


def _safe_error(error: BaseException, secrets: Iterable[str] = ()) -> str:
    message = f"{type(error).__name__}: {error}"
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return message[:1000]


def _empty_result(source_code: str, status: str) -> dict[str, Any]:
    return {
        "source": source_code,
        "status": status,
        "fetched": 0,
        "selected": 0,
        "inserted": 0,
        "updated": 0,
        "deleted": 0,
        "unmatched": 0,
        "invalid": 0,
    }


def _sync_source(
    store: SupabaseIngestionStore,
    *,
    source: str,
    today: date,
    incremental_only: bool = False,
) -> dict[str, Any]:
    """보존기간 안의 누락분을 보완하고 Cron은 전날을 겹쳐 다시 수집합니다."""
    if source not in SOURCE_ORDER:
        raise ValueError(f"지원하지 않는 수집원입니다: {source}")
    source_code = SOURCE_CODES[source]
    window_start = retention_start(today, SOURCE_RETENTION_MONTHS[source])
    window_end = today - timedelta(days=1)
    latest_registered_on = store.latest_registered_on(source_code)
    if incremental_only:
        # 전날 등록분을 다시 읽어 늦게 공개된 항목도 반영합니다.
        start_candidate = min(latest_registered_on or window_start, window_end)
        trigger = "vercel_cron_incremental"
    else:
        start_candidate = min(latest_registered_on or window_start, window_end)
        trigger = "manual_catchup"
    start_date = max(window_start, start_candidate)
    result = _empty_result(source_code, "running")

    try:
        run_id = store.create_run(
            source_code,
            start_date,
            window_end,
            trigger=trigger,
        )
    except RunAlreadyActive:
        return _empty_result(source_code, "already_running")

    audit: dict[str, Any] = {}
    upsert_completed = False
    try:
        category_ids = store.load_category_ids(source_code)
        aliases = store.load_aliases(source_code)
        locations = store.load_locations(source_code)
        if not category_ids:
            raise RuntimeError(f"활성 카테고리 매핑이 없습니다: {source_code}")

        client = create_source_client(source)
        selected_rows, fetch_summary = collect_selected_rows(
            client,
            source=source,
            start_date=start_date,
            end_date=window_end,
            rows_per_page=100,
        )
        result["fetched"] = fetch_summary["fetched_count"]
        result["selected"] = fetch_summary["selected_count"]
        result["invalid"] = fetch_summary["invalid_count"]

        database_rows, unmatched_count = _build_database_rows(
            selected_rows,
            source_code=source_code,
            category_ids=category_ids,
            aliases=aliases,
            locations=locations,
        )
        result["unmatched"] = unmatched_count
        existing = store.existing_identities(source_code)
        incoming = {(row["atc_id"], row["found_sequence"]) for row in database_rows}
        result["updated"] = len(incoming & existing)
        result["inserted"] = len(incoming - existing)

        store.upsert_found_items(database_rows)
        upsert_completed = True
        result["deleted"] = store.delete_expired(source_code)
        result["status"] = "succeeded"
        audit = _audit_values(
            result,
            start_date=start_date,
            end_date=window_end,
            trigger=trigger,
            fetch_summary=fetch_summary,
        )
    except Exception as error:
        result["status"] = "partial" if upsert_completed else "failed"
        error_message = _safe_error(error, (store.secret_key,))
        result["error"] = error_message
        audit = _audit_values(
            result,
            start_date=start_date,
            end_date=window_end,
            trigger=trigger,
            error_message=error_message,
            upsert_completed=upsert_completed,
        )

    _finish_audit(store, run_id, audit, result)
    return result


def _audit_values(
    result: dict[str, Any],
    *,
    start_date: date,
    end_date: date,
    trigger: str,
    fetch_summary: dict[str, int] | None = None,
    error_message: str | None = None,
    upsert_completed: bool = True,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "window_start": start_date.isoformat(),
        "window_end": end_date.isoformat(),
        "trigger": trigger,
    }
    if fetch_summary is not None:
        metadata.update(
            {
                "api_total_count": fetch_summary["api_total_count"],
                "outside_range_count": fetch_summary["outside_range_count"],
            }
        )
    return {
        "status": result["status"],
        "fetched_count": result["fetched"],
        "selected_count": result["selected"],
        "inserted_count": result["inserted"] if upsert_completed else 0,
        "updated_count": result["updated"] if upsert_completed else 0,
        "deleted_count": result["deleted"] if result["status"] == "succeeded" else 0,
        "unmatched_count": result["unmatched"],
        "invalid_count": result["invalid"],
        "error_message": error_message,
        "metadata": metadata,
    }


def _finish_audit(
    store: SupabaseIngestionStore,
    run_id: int,
    audit: dict[str, Any],
    result: dict[str, Any],
) -> None:
    last_finish_error: Exception | None = None
    for _ in range(3):
        try:
            store.finish_run(run_id, audit)
            last_finish_error = None
            break
        except Exception as finish_error:
            last_finish_error = finish_error
    if last_finish_error is None:
        return
    finish_message = _safe_error(last_finish_error, (store.secret_key,))
    if result["status"] == "succeeded":
        result["status"] = "partial"
    result["audit_error"] = finish_message


def run_incremental_sync(
    source: str,
    today: date | None = None,
) -> dict[str, Any]:
    """Vercel Cron용: 누락분과 전날 등록분을 포함해 멱등 동기화합니다."""
    target_date = today or datetime.now(KOREA_TIMEZONE).date()
    result = _sync_source(
        SupabaseIngestionStore.from_environment(),
        source=source,
        today=target_date,
        incremental_only=True,
    )
    return {
        "date": target_date.isoformat(),
        "status": result["status"],
        "source": result,
    }


def run_daily_sync(today: date | None = None) -> dict[str, Any]:
    """수동 복구용: 두 출처의 누락 구간을 보존기간 안에서 보완합니다."""
    target_date = today or datetime.now(KOREA_TIMEZONE).date()
    store = SupabaseIngestionStore.from_environment()
    source_results = [
        _sync_source(store, source=source, today=target_date)
        for source in SOURCE_ORDER
    ]
    return {
        "date": target_date.isoformat(),
        "status": (
            "succeeded"
            if all(result["status"] == "succeeded" for result in source_results)
            else "partial"
        ),
        "sources": source_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="두 경찰청 습득물 API의 6개 카테고리를 Supabase에 동기화합니다."
    )
    parser.add_argument("--today", help="동기화 기준일 YYYY-MM-DD")
    parser.add_argument(
        "--source",
        choices=SOURCE_ORDER,
        help="한 출처만 당일 증분 동기화합니다.",
    )
    args = parser.parse_args()
    target_date = date.fromisoformat(args.today) if args.today else None
    result = (
        run_incremental_sync(args.source, target_date)
        if args.source
        else run_daily_sync(target_date)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
