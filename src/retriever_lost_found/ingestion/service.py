from __future__ import annotations

import argparse
import json
import re
import socket
import ssl
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
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
from ..errors import safe_error as _safe_error
from ..privacy.masking import row_needs_masking
from .categories import FALLBACK_CATEGORY, category_key, category_parent
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
# PRD 4.5 — 2026-11-20 전체 물품 확대 뒤 과거 구간을 채우는 수동 백필.
BACKFILL_TRIGGER = "manual_backfill"
BACKFILL_DEFAULT_CHUNK_DAYS = 7
# Vercel Hobby 크론은 정시부터 그 시간 안에 실행된다: 09·21시대 수집, 11·23시대 임베딩·알림(KST).
# 출처당 running 수집은 하나뿐이라 백필이 겹치면 크론이 건너뛰어지므로 이 시간과 직전 20분엔 새 구간을 시작하지 않는다.
BACKFILL_BLOCKED_HOURS = (9, 11, 21, 23)
BACKFILL_MARGIN_MINUTES = 20


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


def _resolve_category_id(raw_category_name: str, category_ids: dict[str, int]) -> tuple[int | None, bool]:
    """PRD 2.2, 4.5 — 정확 매핑 → 상위분류 정확 일치 → 기타물품. (id, 기타물품 대체 여부)"""
    for key in (category_key(raw_category_name), category_key(category_parent(raw_category_name))):
        if key in category_ids:
            return category_ids[key], False
    return category_ids.get(category_key(FALLBACK_CATEGORY.name)), True


def _build_database_rows(
    selected_rows: list[dict[str, str]],
    *,
    source_code: str,
    category_ids: dict[str, int],
    aliases: dict[str, int],
    locations: list[LocationReference],
    statistics: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    database_rows: list[dict[str, Any]] = []
    resolver = _LocationResolver(aliases, locations)
    category_fallback = 0
    for row in selected_rows:
        category_id, used_fallback = _resolve_category_id(row["raw_category_name"], category_ids)
        if category_id is None:
            # 기타물품 행이 없는 DB(20261120090000 마이그레이션 전)에서는 실패시켜 만료 삭제를 막는다.
            raise RuntimeError(
                f"DB 카테고리 매핑이 없습니다: {source_code}/{row['raw_category_name']}"
            )
        category_fallback += used_fallback
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
    if statistics is not None:
        statistics["category_fallback"] = category_fallback
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
    cleanup_expired: bool = True,
    backfill_window: tuple[date, date] | None = None,
) -> dict[str, Any]:
    """보존기간 안의 누락분을 보완하고 Cron은 전날을 겹쳐 다시 수집합니다.

    backfill_window가 있으면 그 과거 구간만 받아 DB에 없는 물품만 추가합니다(run_backfill).
    """
    sync_started = time.monotonic()
    if incremental_only:
        store.deadline = sync_started + SYNC_WORK_DEADLINE_SECONDS
    if source not in SOURCE_ORDER:
        raise ValueError(f"지원하지 않는 수집원입니다: {source}")
    source_code = SOURCE_CODES[source]
    window_start = retention_start(today, SOURCE_RETENTION_MONTHS[source])
    # PRD 4.5 — collect today's arrivals too; always overlap yesterday.
    window_end = today
    trigger = "vercel_cron_incremental" if incremental_only else "manual_catchup"
    start_date = window_start
    lookup_error = None
    if backfill_window is not None:
        start_date, window_end = backfill_window
        trigger = BACKFILL_TRIGGER
        if start_date < window_start or window_end > today or start_date > window_end:
            raise ValueError("백필 구간은 보존기간 시작일과 오늘 사이여야 합니다.")
    else:
        try:
            latest_registered_on = store.latest_registered_on(source_code)
            start_candidate = min(latest_registered_on or window_start, today - timedelta(days=1))
            start_date = max(window_start, start_candidate)
        except Exception as error:
            # Still create and close an audit, so the embedding barrier can recover.
            lookup_error = error
    result = _empty_result(source_code, "running")
    result["cleanup_enabled"] = cleanup_expired

    try:
        run_id = store.create_run(
            source_code,
            start_date,
            window_end,
            trigger=trigger,
            cleanup_expired=cleanup_expired,
            # PRD 4.5 — 임베딩 회차(오전·오후)는 실행한 날로 판정한다. 일반 실행은 window_end와 같다.
            business_date=today,
        )
    except RunAlreadyActive:
        return _empty_result(source_code, "already_running")

    audit: dict[str, Any] = {}
    upsert_completed = False
    client = None
    try:
        if lookup_error is not None:
            raise lookup_error
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

        category_statistics: dict[str, int] = {}
        database_rows = _build_database_rows(
            selected_rows,
            source_code=source_code,
            category_ids=category_ids,
            aliases=aliases,
            locations=locations,
            statistics=category_statistics,
        )
        result["category_fallback"] = category_statistics["category_fallback"]
        existing: set[tuple[str, str]] | None = None
        if backfill_window is not None:
            # 백필은 DB에 없는 물품만 추가한다. 기존 물품의 위치·상세정보는 일일 수집이 관리하므로
            # 다시 쓰지 않고, 상세 조회 예산도 새 물품에만 쓴다.
            existing_before = store.existing_identities(
                source_code, {(row["atc_id"], row["found_sequence"]) for row in database_rows}
            )
            database_rows = [
                row for row in database_rows
                if (row["atc_id"], row["found_sequence"]) not in existing_before
            ]
            result["skipped_existing"] = len(existing_before)
            existing = set()
        if any(row["location_match_status"] == "ambiguous" for row in database_rows):
            mappings = store.load_detail_mappings(source_code, active_ids={location.id for location in locations})
            detail_deadline = time.monotonic() + DETAIL_BUDGET_SECONDS
            if backfill_window is None:
                # Vercel 함수 300초 안에 끝나도록 크론·수동 복구는 실행 시작 기준 마감도 지킨다.
                detail_deadline = min(detail_deadline, sync_started + DETAIL_STAGE_DEADLINE_SECONDS)
            result["detail_lookup"] = _enrich_ambiguous_rows(
                database_rows, source_code=source_code, service_key=client.service_key,
                mappings=mappings, deadline=detail_deadline,
            )
        # PRD 3.3 · Q5 — 이번에 저장할 물품 중 출력할 때 가려지는 물품 수(건수만, 텍스트는 남기지 않는다).
        # 평소 값과 크게 다르면 마스킹 회귀나 원천 데이터 형식 변화를 의심한다.
        result["masked"] = sum(row_needs_masking(row) for row in database_rows)
        incoming = {(row["atc_id"], row["found_sequence"]) for row in database_rows}
        if existing is None:
            existing = store.existing_identities(source_code, incoming)
        result["updated"] = len(incoming & existing)
        result["inserted"] = len(incoming - existing)

        # PRD 4.5 — count final DB matches after the verified-detail trigger runs.
        upsert_statistics: dict[str, int] = {}
        result["unmatched"] = store.upsert_found_items(database_rows, statistics=upsert_statistics)
        result["excluded_overseas"] = upsert_statistics.get("excluded_overseas", 0)
        upsert_completed = True
        # PRD 4.5 — the afternoon cron only ingests; cleanup stays in the morning.
        if cleanup_expired:
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
        "business_date": end_date.isoformat(),
        "upsert_completed": upsert_completed,
        "window_start": start_date.isoformat(),
        "window_end": end_date.isoformat(),
        "trigger": trigger,
        "cleanup_enabled": result["cleanup_enabled"],
        "excluded_overseas_count": result.get("excluded_overseas", 0),
    }
    if "category_fallback" in result:
        # PRD 4.5 — 상위분류가 목록에 없어 기타물품으로 저장한 건수. 0이 아니면 분류 목록을 늘린다.
        metadata["category_fallback_count"] = result["category_fallback"]
    if "masked" in result:
        metadata["masked_count"] = result["masked"]
    if "skipped_existing" in result:
        # 백필에서 이미 DB에 있어 다시 쓰지 않은 물품 수.
        metadata["skipped_existing_count"] = result["skipped_existing"]
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
    *,
    cleanup_expired: bool = True,
) -> dict[str, Any]:
    """Vercel Cron용: 누락분과 전날 등록분을 포함해 멱등 동기화합니다."""
    target_date = today or datetime.now(KOREA_TIMEZONE).date()
    result = _sync_source(
        SupabaseIngestionStore.from_environment(),
        source=source,
        today=target_date,
        incremental_only=True,
        cleanup_expired=cleanup_expired,
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


def _backfill_blocked(now: datetime) -> bool:
    """크론 시간대(KST 09·11·21·23시대)와 그 직전 20분이면 새 백필 구간을 시작하지 않습니다."""
    local = now.astimezone(KOREA_TIMEZONE)
    if local.hour in BACKFILL_BLOCKED_HOURS:
        return True
    return (local.hour + 1) % 24 in BACKFILL_BLOCKED_HOURS and local.minute >= 60 - BACKFILL_MARGIN_MINUTES


def _backfill_windows(
    start: date, end: date, *, today: date, source: str, chunk_days: int = BACKFILL_DEFAULT_CHUNK_DAYS,
) -> list[tuple[date, date]]:
    """백필 구간을 chunk_days일씩 나눕니다. 보존기간 밖이나 오늘 이후는 거부합니다."""
    if not 1 <= chunk_days <= 31:
        raise ValueError("백필 구간 길이는 1~31일이어야 합니다.")
    if start > end:
        raise ValueError("백필 시작일은 종료일보다 늦을 수 없습니다.")
    window_start = retention_start(today, SOURCE_RETENTION_MONTHS[source])
    if start < window_start:
        # 보존기간 밖 물품은 다음 오전 정리에서 지워지므로 넣지 않는다(PRD 3.1-2).
        raise ValueError(f"백필 시작일은 보존기간 시작일({window_start.isoformat()}) 이후여야 합니다.")
    if end > today:
        raise ValueError("백필 종료일은 오늘 이후일 수 없습니다.")
    windows: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=chunk_days - 1))
        windows.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return windows


def run_backfill(
    store: SupabaseIngestionStore,
    *,
    sources: tuple[str, ...],
    start: date,
    end: date,
    today: date,
    chunk_days: int = BACKFILL_DEFAULT_CHUNK_DAYS,
    now: Any = None,
) -> dict[str, Any]:
    """PRD 4.5 — 과거 구간을 출처·구간별 실행으로 나눠 DB에 없는 물품만 추가합니다.

    실패하거나 크론 시간대에 들어가면 멈추고 다시 시작할 출처·시작일(`resume`)을 돌려줍니다.
    """
    clock = now or (lambda: datetime.now(KOREA_TIMEZONE))
    plans = {
        source: _backfill_windows(start, end, today=today, source=source, chunk_days=chunk_days)
        for source in sources
    }
    chunks: list[dict[str, Any]] = []

    def summary(status: str, resume: tuple[str, date] | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": status,
            "today": today.isoformat(),
            "inserted": sum(chunk.get("inserted", 0) for chunk in chunks),
            "skipped_existing": sum(chunk.get("skipped_existing", 0) for chunk in chunks),
            "unmatched": sum(chunk.get("unmatched", 0) for chunk in chunks),
            "chunks": chunks,
        }
        if resume is not None:
            result["resume"] = {"source": resume[0], "from": resume[1].isoformat()}
        return result

    for source in sources:
        for window in plans[source]:
            if _backfill_blocked(clock()):
                return summary("stopped", (source, window[0]))
            chunk = _sync_source(store, source=source, today=today, backfill_window=window)
            chunks.append({**chunk, "window": [window[0].isoformat(), window[1].isoformat()]})
            print(
                f"{source} {window[0]}~{window[1]}: {chunk['status']}, "
                f"추가 {chunk.get('inserted', 0):,}건, 기존 {chunk.get('skipped_existing', 0):,}건",
                file=sys.stderr,
                flush=True,
            )
            if chunk["status"] != "succeeded":
                return summary("failed", (source, window[0]))
    return summary("succeeded")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="두 경찰청 습득물 API의 습득물 전체를 Supabase에 동기화합니다."
    )
    parser.add_argument("--today", help="동기화 기준일 YYYY-MM-DD")
    parser.add_argument(
        "--source",
        choices=SOURCE_ORDER,
        help="한 출처만 당일 증분 동기화합니다. 백필에서는 그 출처만 채웁니다.",
    )
    parser.add_argument(
        "--backfill-from",
        help="과거 구간 백필 시작일 YYYY-MM-DD. DB에 없는 물품만 추가합니다(크론 시간대엔 멈춤).",
    )
    parser.add_argument("--backfill-to", help="백필 종료일 YYYY-MM-DD (기본: 어제)")
    parser.add_argument(
        "--chunk-days", type=int, default=BACKFILL_DEFAULT_CHUNK_DAYS,
        help="백필 실행 하나가 맡을 일수 (기본 7일)",
    )
    args = parser.parse_args()
    target_date = date.fromisoformat(args.today) if args.today else None
    if args.backfill_from:
        today = target_date or datetime.now(KOREA_TIMEZONE).date()
        result = run_backfill(
            SupabaseIngestionStore.from_environment(),
            sources=(args.source,) if args.source else SOURCE_ORDER,
            start=date.fromisoformat(args.backfill_from),
            end=date.fromisoformat(args.backfill_to) if args.backfill_to else today - timedelta(days=1),
            today=today,
            chunk_days=args.chunk_days,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "succeeded" else 1
    result = (
        run_incremental_sync(args.source, target_date)
        if args.source
        else run_daily_sync(target_date)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
