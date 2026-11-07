from __future__ import annotations

import hmac
import sys
import time
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..alerts import matching
from ..alerts.dispatch import run_send_pass
from ..alerts.matching import run_match_pass
from ..alerts.sender import SmtpEmailSender
from ..alerts.settings import load_alert_settings
from ..alerts.store import SupabaseAlertStore
from ..config import read_optional_env_value
from ..ingestion.collector import SOURCE_CODES
from ..ingestion.service import run_incremental_sync


router = APIRouter(prefix="/api/cron", include_in_schema=False)
# 설계 6.1·6.2 — 함수 300초 제한 안에서 기록·응답에 40초를 남긴다.
ALERT_DEADLINE_SECONDS = 260.0


def _authorized(request: Request) -> JSONResponse | None:
    cron_secret = read_optional_env_value("CRON_SECRET")
    if not cron_secret:
        return JSONResponse(
            status_code=503,
            content={"error": "CRON_SECRET이 설정되지 않았습니다."},
        )
    authorization = request.headers.get("authorization", "")
    if not hmac.compare_digest(authorization, f"Bearer {cron_secret}"):
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    return None


def _run_alert_matching(source: str, result: dict[str, Any], *, deadline: float) -> None:
    """설계 6.1 — 알림 매칭. 실패해도 수집 응답과 ingestion_runs는 그대로다."""
    judge = matching.SIMILARITY_JUDGE
    source_result = result.get("source") if isinstance(result.get("source"), dict) else {}
    try:
        inserted = int(source_result.get("inserted") or 0)
    except (TypeError, ValueError):
        inserted = 0
    # 판정이 없거나, 저장까지 못 했거나, 새 물품이 없으면 DB를 부르지 않는다.
    if judge is None or result.get("status") not in ("succeeded", "partial") or inserted <= 0:
        return
    try:
        store = SupabaseAlertStore.from_environment()
        run_match_pass(store, judge, item_source_code=SOURCE_CODES[source], deadline=deadline)
    except Exception as error:
        print(f"[alerts] {source} 매칭 단계 실패: {type(error).__name__}", file=sys.stderr)


def _run_source(request: Request, source: Literal["partner", "police"]) -> JSONResponse:
    started = time.monotonic()
    authorization_error = _authorized(request)
    if authorization_error is not None:
        return authorization_error
    result = run_incremental_sync(source)
    _run_alert_matching(source, result, deadline=started + ALERT_DEADLINE_SECONDS)
    return JSONResponse(
        status_code=200 if result.get("status") == "succeeded" else 500,
        content=result,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/partner-sync")
def partner_sync(request: Request) -> JSONResponse:
    """연계기관 API의 오늘 등록분만 동기화합니다."""
    return _run_source(request, "partner")


@router.get("/police-sync")
def police_sync(request: Request) -> JSONResponse:
    """경찰관서 API의 오늘 등록분만 동기화합니다."""
    return _run_source(request, "police")


@router.get("/alert-send")
def alert_send(request: Request) -> JSONResponse:
    """설계 6.2 — 만료 안내 → 발송(450통 한도) → 정리. KST 11시대에 한 번 돈다."""
    started = time.monotonic()
    authorization_error = _authorized(request)
    if authorization_error is not None:
        return authorization_error
    no_store = {"Cache-Control": "no-store"}
    try:
        settings = load_alert_settings()
    except ValueError as error:
        return JSONResponse(status_code=503, content={"error": str(error)}, headers=no_store)
    if settings is None:
        return JSONResponse(status_code=503, content={"error": "알림 메일 설정이 없습니다."}, headers=no_store)
    result = run_send_pass(
        SupabaseAlertStore.from_environment(),
        SmtpEmailSender(settings.mail),
        settings,
        deadline=started + ALERT_DEADLINE_SECONDS,
    )
    return JSONResponse(
        status_code=500 if result.get("status") == "failed" else 200,
        content=result,
        headers=no_store,
    )
