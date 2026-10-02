from __future__ import annotations

import argparse
import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
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
DETAIL_ENDPOINTS = {
    "partner_api": "LosPtfundInfoInqireService/getPtLosfundDetailInfo",
    "police_api": "LosfundInfoInqireService/getLosfundDetailInfo",
}


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


def _fetch_detail(
    source_code: str, service_key: str, atc_id: str, found_sequence: str
) -> dict[str, str]:
    """PRD 4.5 — 상세 기관 코드와 전화번호를 원본 API에서 읽습니다."""
    url = "https://apis.data.go.kr/1320000/" + DETAIL_ENDPOINTS[source_code]
    url += "?" + urllib.parse.urlencode(
        {"serviceKey": service_key, "ATC_ID": atc_id, "FD_SN": found_sequence}
    )
    for attempt in range(2):
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                root = ET.fromstring(response.read())
            if root.findtext("./header/resultCode") != "00":
                raise ValueError("상세 API 오류")
            item = root.find("./body/item")
            if item is None:
                item = root.find("./body/items/item")
            if item is None:
                raise ValueError("상세 API 물품 없음")
            return {
                "detail_org_id": (item.findtext("orgId") or "").strip(),
                "detail_org_name": (item.findtext("orgNm") or "").strip(),
                "detail_department": (item.findtext("depPlace") or "").strip(),
                "detail_phone": (item.findtext("tel") or "").strip(),
            }
        except (urllib.error.URLError, TimeoutError, ET.ParseError, ValueError):
            if attempt == 1:
                raise
            time.sleep(2**attempt)
    raise AssertionError("도달할 수 없는 코드")


def _build_database_rows(
    selected_rows: list[dict[str, str]],
    *,
    source_code: str,
    category_ids: dict[str, int],
    aliases: dict[str, int],
    locations: list[LocationReference],
    detail_mappings: dict[tuple[str, str], int],
    detail_evidence: dict[tuple[str, str], dict[str, Any]],
    detail_service_key: str,
    detail_fetcher: Callable[[str, str, str, str], dict[str, str]] = _fetch_detail,
    detail_deadline: float | None = None,
) -> tuple[list[dict[str, Any]], int, int]:
    database_rows: list[dict[str, Any]] = []
    unmatched_count = 0
    detail_errors = 0
    active_ids = {location.id for location in locations}
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
        evidence = detail_evidence.get((row["atc_id"], row["found_sequence"]))
        if evidence and _normalize_key(evidence["raw_storage_name"]) != _normalize_key(row["raw_storage_name"]):
            evidence = None
        if (match_status == "ambiguous" and not evidence and detail_errors < 3
                and (detail_deadline is None or time.monotonic() < detail_deadline)):
            try:
                evidence = detail_fetcher(
                    source_code, detail_service_key, row["atc_id"], row["found_sequence"]
                )
                evidence["detail_checked_at"] = datetime.now().astimezone().isoformat()
                time.sleep(0.35)
            except (urllib.error.URLError, TimeoutError, ET.ParseError, ValueError):
                detail_errors += 1
        if match_status == "ambiguous" and evidence:
            candidate = detail_mappings.get(
                (_normalize_key(row["raw_storage_name"]), str(evidence.get("detail_org_id") or ""))
            )
            if candidate in active_ids:
                location_id, match_status = candidate, "matched"
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
                "detail_org_id": (evidence or {}).get("detail_org_id") or None,
                "detail_org_name": (evidence or {}).get("detail_org_name") or None,
                "detail_department": (evidence or {}).get("detail_department") or None,
                "detail_phone": (evidence or {}).get("detail_phone") or None,
                "detail_checked_at": (evidence or {}).get("detail_checked_at"),
            }
        )
    return database_rows, unmatched_count, detail_errors


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


def resolve_existing_ambiguous(
    store: SupabaseIngestionStore,
    source_code: str,
    service_key: str,
    *,
    detail_limit: int = 20,
    detail_deadline: float | None = None,
) -> dict[str, int]:
    """PRD 4.5 — 기존 증거에 새 규칙을 적용하고 누락된 상세정보를 재시도합니다."""
    mappings = store.load_detail_mappings(source_code)
    active_ids = {location.id for location in store.load_locations(source_code)}
    result = {"resolved": 0, "fetched": 0, "errors": 0}
    for item in store.load_ambiguous_items(source_code):
        values: dict[str, Any] = {}
        if (not item["detail_checked_at"] and result["fetched"] < detail_limit
                and result["errors"] < 3
                and (detail_deadline is None or time.monotonic() < detail_deadline)):
            try:
                values = _fetch_detail(
                    source_code, service_key, item["atc_id"], item["found_sequence"]
                )
                values["detail_checked_at"] = datetime.now().astimezone().isoformat()
                result["fetched"] += 1
                time.sleep(0.35)
            except (urllib.error.URLError, TimeoutError, ET.ParseError, ValueError):
                result["errors"] += 1
                continue
        org_id = str(values.get("detail_org_id") or item["detail_org_id"] or "")
        location_id = mappings.get((_normalize_key(item["raw_storage_name"]), org_id))
        if location_id in active_ids:
            values.update(storage_location_id=location_id, location_match_status="matched")
            result["resolved"] += 1
        if values:
            store.update_ambiguous_item(int(item["id"]), values)
    return result


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
        detail_mappings = store.load_detail_mappings(source_code)
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

        # PRD 5 — 상세 API 지연이 목록 저장과 Vercel 300초 제한을 막지 않게 합니다.
        detail_deadline = time.monotonic() + 20
        result["backlog"] = resolve_existing_ambiguous(
            store, source_code, client.service_key, detail_deadline=detail_deadline
        )
        detail_evidence = store.load_detail_evidence(source_code)

        database_rows, unmatched_count, detail_errors = _build_database_rows(
            selected_rows,
            source_code=source_code,
            category_ids=category_ids,
            aliases=aliases,
            locations=locations,
            detail_mappings=detail_mappings,
            detail_evidence=detail_evidence,
            detail_service_key=client.service_key,
            detail_deadline=detail_deadline,
        )
        result["unmatched"] = unmatched_count
        result["detail_errors"] = detail_errors
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
                "detail_errors": result.get("detail_errors", 0),
                "backlog": result.get("backlog", {}),
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
    parser.add_argument("--resolve-ambiguous", action="store_true")
    parser.add_argument("--detail-limit", type=int, default=100)
    args = parser.parse_args()
    if args.resolve_ambiguous:
        if not args.source or args.detail_limit < 0:
            parser.error("--resolve-ambiguous에는 --source와 0 이상의 --detail-limit이 필요합니다.")
        result = resolve_existing_ambiguous(
            SupabaseIngestionStore.from_environment(),
            SOURCE_CODES[args.source],
            create_source_client(args.source).service_key,
            detail_limit=args.detail_limit,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
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
