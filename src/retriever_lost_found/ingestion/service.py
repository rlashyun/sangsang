from __future__ import annotations

import argparse
import json
import re
import socket
import ssl
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone
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
from .overseas import is_overseas_custody


SOURCE_ORDER = ("partner", "police")
DETAIL_ENDPOINTS = {
    "police_api": "LosfundInfoInqireService/getLosfundDetailInfo",
    "partner_api": "LosPtfundInfoInqireService/getPtLosfundDetailInfo",
}
# PRD 4.5, 5 — keep optional detail work inside the 300-second cron budget.
DETAIL_WORKERS = 4
DETAIL_TIMEOUT_SECONDS = 3.0
DETAIL_BUDGET_SECONDS = 60.0
# PRD 4.5 — 2026-11-08: include retries within 400 attempts; retain time deadlines.
DETAIL_MAX_REQUESTS = 400
DETAIL_STAGE_DEADLINE_SECONDS = 180.0
COLLECTION_STAGE_DEADLINE_SECONDS = 150.0
SYNC_WORK_DEADLINE_SECONDS = 270.0
AUDIT_DEADLINE_SECONDS = 295.0


class DetailLookupError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False, reason: str = "unknown") -> None:
        super().__init__(message)
        self.retryable = retryable
        self.reason = reason


def _normalize_key(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _base_name(value: Any) -> str:
    without_notes = re.sub(r"\([^)]*\)|\[[^]]*\]", "", str(value or ""))
    return _normalize_key(without_notes)


class _LocationResolver:
    """한 수집 배치에서 기관 색인과 장소명별 결과를 재사용합니다."""

    def __init__(self, aliases: dict[str, int], locations: list[LocationReference]):
        active_ids = {location.id for location in locations}
        self.aliases = {key: value for key, value in aliases.items() if value in active_ids}
        self.exact: dict[str, set[int]] = {}
        self.base: dict[str, set[int]] = {}
        self.suffix: list[tuple[str, int]] = []
        self.cache: dict[str, tuple[int | None, str]] = {}
        for location in locations:
            for key in {_normalize_key(location.name), _normalize_key(location.normalized_name)}:
                if key:
                    self.exact.setdefault(key, set()).add(location.id)
            base = _base_name(location.name)
            if base:
                self.base.setdefault(base, set()).add(location.id)
            key = _normalize_key(location.normalized_name)
            if key:
                self.suffix.append((key, location.id))

    @staticmethod
    def _result(candidates: set[int]) -> tuple[int | None, str]:
        if len(candidates) == 1:
            return next(iter(candidates)), "matched"
        return None, "ambiguous" if candidates else "unmatched"

    def resolve(self, raw_storage_name: str) -> tuple[int | None, str]:
        normalized = _normalize_key(raw_storage_name)
        if not normalized:
            return None, "unmatched"
        if normalized in self.cache:
            return self.cache[normalized]
        if normalized in self.aliases:
            result = self.aliases[normalized], "matched"
        elif normalized in self.exact:
            result = self._result(self.exact[normalized])
        elif normalized in self.base:
            result = self._result(self.base[normalized])
        else:
            result = self._result({
                location_id for key, location_id in self.suffix
                if key.endswith(normalized) or normalized.endswith(key)
            })
        self.cache[normalized] = result
        return result


def _resolve_location(
    raw_storage_name: str, aliases: dict[str, int], locations: list[LocationReference],
) -> tuple[int | None, str]:
    return _LocationResolver(aliases, locations).resolve(raw_storage_name)


def _build_database_rows(
    selected_rows: list[dict[str, str]],
    *,
    source_code: str,
    category_ids: dict[str, int],
    aliases: dict[str, int],
    locations: list[LocationReference],
) -> list[dict[str, Any]]:
    database_rows: list[dict[str, Any]] = []
    resolver = _LocationResolver(aliases, locations)
    for row in selected_rows:
        normalized_category = _normalize_key(row["raw_category_name"])
        category_id = category_ids.get(normalized_category)
        if category_id is None:
            raise RuntimeError(
                f"DB 카테고리 매핑이 없습니다: {source_code}/{row['raw_category_name']}"
            )
        if is_overseas_custody(source_code, row["raw_storage_name"]):
            location_id, match_status = None, "excluded_overseas"
        else:
            location_id, match_status = resolver.resolve(row["raw_storage_name"])
        # PRD 4.5 — the bounded detail stage handles only ambiguous rows afterwards.
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
                "detail_org_id": None,
                "detail_org_name": None,
                "detail_department": None,
                "detail_phone": None,
                "detail_checked_at": None,
                # PostgREST bulk upsert requires identical keys for enriched and plain rows.
                "detail_status": None,
            }
        )
    return database_rows


def _fetch_detail(source_code: str, service_key: str, atc_id: str, sequence: str) -> dict[str, Any]:
    """Read current custody evidence; never expose URLs, keys, or raw API errors."""
    url = "https://apis.data.go.kr/1320000/" + DETAIL_ENDPOINTS[source_code]
    url += "?" + urllib.parse.urlencode({"serviceKey": service_key, "ATC_ID": atc_id, "FD_SN": sequence})
    try:
        with urllib.request.urlopen(url, timeout=DETAIL_TIMEOUT_SECONDS) as response:
            payload = response.read(131_073)
        if len(payload) > 131_072:
            raise DetailLookupError("상세 응답 크기 초과", reason="oversized_response")
        root = ET.fromstring(payload)
        result_code = root.findtext("./header/resultCode")
        if result_code != "00":
            # PRD 5 — retain only bounded numeric codes, never response text or URLs.
            api_code = root.findtext(".//returnReasonCode") or result_code
            code = api_code if api_code and re.fullmatch(r"[0-9]{1,3}", api_code) else "unknown"
            raise DetailLookupError("상세 API 오류 응답", reason=f"api_{code}")
        item = root.find("./body/item")
        if item is None:
            item = root.find("./body/items/item")
        if item is None:
            raise DetailLookupError("상세 물품 누락", reason="missing_item")
        if item.findtext("atcId") != atc_id or item.findtext("fdSn") != sequence:
            raise DetailLookupError("상세 물품 식별 불일치", reason="identity_mismatch")
        values = {field: (item.findtext(field) or "").strip() for field in ("orgId", "orgNm", "depPlace", "tel", "csteSteNm")}
        if not values["orgId"] or not values["depPlace"]:
            raise DetailLookupError("현재 보관기관 정보 누락", reason="missing_custody_identity")
        return {
            "detail_org_id": values["orgId"], "detail_org_name": values["orgNm"],
            "detail_department": values["depPlace"], "detail_phone": values["tel"],
            "detail_status": values["csteSteNm"],
            "detail_checked_at": datetime.now(timezone.utc).isoformat(),
        }
    except DetailLookupError:
        raise
    except Exception as error:
        status = error.code if isinstance(error, urllib.error.HTTPError) else None
        retryable = status == 429 or status >= 500 if isinstance(status, int) else isinstance(error, (TimeoutError, urllib.error.URLError))
        if isinstance(status, int):
            reason = f"http_{status}"
        elif isinstance(error, TimeoutError) or isinstance(getattr(error, "reason", None), TimeoutError):
            reason = "timeout"
        elif isinstance(error, urllib.error.URLError):
            network_error = error.reason
            if isinstance(network_error, ssl.SSLCertVerificationError):
                reason = "tls_certificate"
            elif isinstance(network_error, ssl.SSLError):
                reason = "tls"
            elif isinstance(network_error, socket.gaierror):
                reason = "dns"
            elif isinstance(network_error, ConnectionResetError):
                reason = "connection_reset"
            else:
                reason = "network"
        elif isinstance(error, ET.ParseError):
            reason = "invalid_xml"
        else:
            reason = "unexpected"
        raise DetailLookupError("상세 조회 실패", retryable=retryable, reason=reason) from None


def _enrich_ambiguous_rows(
    rows: list[dict[str, Any]], *, source_code: str, service_key: str,
    mappings: dict[tuple[str, str], int], deadline: float,
) -> dict[str, Any]:
    """PRD 4.5 — current place+code lookup; treatment status is informational only."""
    started = time.monotonic()
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if row["location_match_status"] == "ambiguous":
            grouped.setdefault((row["atc_id"], row["found_sequence"]), []).append(row)
            # A failed/deferred lookup must not reconnect using stale cached evidence.
            row["location_match_status"] = "unmatched"
            row["storage_location_id"] = None
    stats = {"eligible": len(grouped), "requested": 0, "succeeded": 0, "failed": 0, "deferred": 0, "retried": 0}
    # PRD 4.5, 5 — count failed attempts, including those later recovered by a retry.
    failure_attempts_by_reason: dict[str, int] = {}
    identities = iter(grouped)
    retries: deque[tuple[str, str]] = deque()
    attempts: dict[tuple[str, str], int] = {}
    pending: dict[Any, tuple[str, str]] = {}
    executor = ThreadPoolExecutor(max_workers=DETAIL_WORKERS)
    try:
        while time.monotonic() < deadline:
            while len(pending) < DETAIL_WORKERS and stats["requested"] < DETAIL_MAX_REQUESTS and time.monotonic() < deadline:
                identity = next(identities, None)
                if identity is None and retries:
                    identity = retries.popleft()
                if identity is None:
                    break
                attempts[identity] = attempts.get(identity, 0) + 1
                if attempts[identity] > 1:
                    stats["retried"] += 1
                future = executor.submit(_fetch_detail, source_code, service_key, *identity)
                pending[future] = identity
                stats["requested"] += 1
            if not pending:
                break
            completed, _ = wait(pending, timeout=max(0.0, deadline - time.monotonic()), return_when=FIRST_COMPLETED)
            if not completed:
                break
            for future in completed:
                identity = pending.pop(future)
                try:
                    detail = future.result()
                except Exception as error:
                    reason = error.reason if isinstance(error, DetailLookupError) else "unexpected"
                    failure_attempts_by_reason[reason] = failure_attempts_by_reason.get(reason, 0) + 1
                    if attempts[identity] == 1:
                        stats["failed"] += 1
                    retryable = getattr(error, "retryable", isinstance(error, (TimeoutError, urllib.error.URLError)))
                    # Retry a transient failure once, after first attempts, inside the same caps.
                    if retryable and attempts[identity] == 1 and deadline - time.monotonic() > DETAIL_TIMEOUT_SECONDS:
                        retries.append(identity)
                    continue
                if attempts[identity] > 1:
                    stats["failed"] -= 1
                stats["succeeded"] += 1
                overseas = is_overseas_custody(source_code, detail["detail_department"])
                target = None if overseas else mappings.get((_normalize_key(detail["detail_department"]), detail["detail_org_id"]))
                for row in grouped[identity]:
                    row.update(detail)
                    row["storage_location_id"] = target
                    row["location_match_status"] = (
                        "excluded_overseas" if overseas else "matched" if target is not None else "unmatched"
                    )
    finally:
        for future in pending:
            future.cancel()
        # Fetch workers never write rows/DB. Do not wait past the scheduling deadline.
        executor.shutdown(wait=False, cancel_futures=True)
    stats["deferred"] = stats["eligible"] - stats["succeeded"] - stats["failed"]
    stats["elapsed_seconds"] = round(time.monotonic() - started, 3)
    stats["failure_attempts_by_reason"] = failure_attempts_by_reason
    return stats


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
        "excluded_overseas": 0,
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
    sync_started = time.monotonic()
    if incremental_only:
        store.deadline = sync_started + SYNC_WORK_DEADLINE_SECONDS
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
    client = None
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
            deadline=sync_started + COLLECTION_STAGE_DEADLINE_SECONDS if incremental_only else None,
        )
        result["fetched"] = fetch_summary["fetched_count"]
        result["selected"] = fetch_summary["selected_count"]
        result["invalid"] = fetch_summary["invalid_count"]

        database_rows = _build_database_rows(
            selected_rows,
            source_code=source_code,
            category_ids=category_ids,
            aliases=aliases,
            locations=locations,
        )
        if any(row["location_match_status"] == "ambiguous" for row in database_rows):
            mappings = store.load_detail_mappings(source_code, active_ids={location.id for location in locations})
            result["detail_lookup"] = _enrich_ambiguous_rows(
                database_rows, source_code=source_code, service_key=client.service_key,
                mappings=mappings, deadline=min(
                    time.monotonic() + DETAIL_BUDGET_SECONDS,
                    sync_started + DETAIL_STAGE_DEADLINE_SECONDS,
                ),
            )
        incoming = {(row["atc_id"], row["found_sequence"]) for row in database_rows}
        existing = store.existing_identities(source_code, incoming)
        result["updated"] = len(incoming & existing)
        result["inserted"] = len(incoming - existing)

        # PRD 4.5 — count final DB matches after the verified-detail trigger runs.
        upsert_statistics: dict[str, int] = {}
        result["unmatched"] = store.upsert_found_items(database_rows, statistics=upsert_statistics)
        result["excluded_overseas"] = upsert_statistics.get("excluded_overseas", 0)
        upsert_completed = True
        result["deleted"] = store.delete_expired(source_code)
        result["cleanup_deferred"] = getattr(store, "cleanup_deferred", False) is True
        result["status"] = "succeeded"
        result["duration_seconds"] = round(time.monotonic() - sync_started, 3)
        audit = _audit_values(
            result,
            start_date=start_date,
            end_date=window_end,
            trigger=trigger,
            fetch_summary=fetch_summary,
        )
    except Exception as error:
        result["status"] = "partial" if upsert_completed else "failed"
        service_key = getattr(client, "service_key", "")
        error_message = _safe_error(error, (store.secret_key, service_key if isinstance(service_key, str) else ""))
        result["error"] = error_message
        result["duration_seconds"] = round(time.monotonic() - sync_started, 3)
        audit = _audit_values(
            result,
            start_date=start_date,
            end_date=window_end,
            trigger=trigger,
            error_message=error_message,
            upsert_completed=upsert_completed,
        )

    if incremental_only:
        store.deadline = sync_started + AUDIT_DEADLINE_SECONDS
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
        "excluded_overseas_count": result.get("excluded_overseas", 0),
    }
    if fetch_summary is not None:
        metadata.update(
            {
                "api_total_count": fetch_summary["api_total_count"],
                "outside_range_count": fetch_summary["outside_range_count"],
                "detail_lookup_policy": "bounded_ambiguous_current_place",
            }
        )
    if "detail_lookup" in result:
        metadata["detail_lookup"] = result["detail_lookup"]
    if "duration_seconds" in result:
        metadata["duration_seconds"] = result["duration_seconds"]
    if result.get("cleanup_deferred"):
        metadata["cleanup_deferred"] = True
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
