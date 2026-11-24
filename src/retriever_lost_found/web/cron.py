from __future__ import annotations

import hmac
import sys
import time
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse

from ..alerts import matching
from ..alerts.dispatch import run_send_pass
from ..alerts.matching import run_match_pass
from ..alerts.sender import create_email_sender
from ..alerts.settings import load_alert_settings
from ..alerts.store import AlertStore, SupabaseAlertStore
from ..config import read_optional_env_value
from ..ingestion.collector import SOURCE_CODES
from ..ingestion.service import SOURCE_ORDER, run_incremental_sync
from ..embedding.orchestration import dispatch_embeddings, process_pending_embeddings
from ..thumbnails.worker import dispatch_thumbnails, process_pending as process_pending_thumbnails


router = APIRouter(prefix="/api/cron", include_in_schema=False)
# 설계 6.2 — 함수 300초 제한 안에서 기록·응답에 40초를 남긴다. 매칭은 앞의 120초 안에서 하고
# 남은 시간을 발송에 쓴다. 다 못 본 예약은 커서가 오래된 순으로 다음 날 먼저 본다.
ALERT_MATCH_DEADLINE_SECONDS = 120.0
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


def _run_source(
    request: Request, source: Literal["partner", "police"], background_tasks: BackgroundTasks,
    *, cleanup_expired: bool = True,
) -> JSONResponse:
    authorization_error = _authorized(request)
    if authorization_error is not None:
        return authorization_error
    result = run_incremental_sync(source, cleanup_expired=cleanup_expired)
    # The DB completion trigger provides the two-source barrier. Lost HTTP
    # delivery leaves a pending job for the 11:00/23:00 KST recovery crons (PRD 4.5).
    if result.get("status") != "already_running":
        dispatch_embeddings()
        background_tasks.add_task(dispatch_thumbnails)
    return JSONResponse(
        status_code=200 if result.get("status") == "succeeded" else 500,
        content=result,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/partner-sync")
def partner_sync(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    """오늘까지 연계기관 등록분을 증분 동기화하고 만료 물품을 정리합니다."""
    return _run_source(request, "partner", background_tasks)


@router.get("/police-sync")
def police_sync(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    """오늘까지 경찰관서 등록분을 증분 동기화하고 만료 물품을 정리합니다."""
    return _run_source(request, "police", background_tasks)


@router.get("/partner-sync-afternoon")
def partner_sync_afternoon(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    """오늘까지 연계기관 등록분을 증분 동기화하며 삭제하지 않습니다."""
    return _run_source(request, "partner", background_tasks, cleanup_expired=False)


@router.get("/police-sync-afternoon")
def police_sync_afternoon(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    """오늘까지 경찰관서 등록분을 증분 동기화하며 삭제하지 않습니다."""
    return _run_source(request, "police", background_tasks, cleanup_expired=False)


@router.post("/embedding-sync")
def embedding_callback(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    authorization_error = _authorized(request)
    if authorization_error is not None:
        return authorization_error
    background_tasks.add_task(process_pending_embeddings)
    return JSONResponse(status_code=202, content={"status": "accepted"},
                        headers={"Cache-Control": "no-store"})


@router.get("/embedding-sync")
@router.get("/embedding-sync-afternoon")
def embedding_recovery(request: Request) -> JSONResponse:
    authorization_error = _authorized(request)
    if authorization_error is not None:
        return authorization_error
    result = process_pending_embeddings(recover=True)
    return JSONResponse(status_code=200 if result.get("status") in
                        ("succeeded", "pending", "waiting_or_idle") else 500,
                        content=result, headers={"Cache-Control": "no-store"})


@router.post("/thumbnail-sync")
def thumbnail_callback(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    # PRD 5 — 다른 크론과 같은 비밀값 검증을 거친다.
    authorization_error = _authorized(request)
    if authorization_error is not None:
        return authorization_error
    background_tasks.add_task(process_pending_thumbnails)
    return JSONResponse(status_code=202, content={"status": "accepted"}, headers={"Cache-Control": "no-store"})


def _run_alert_matching(store: AlertStore, *, deadline: float) -> dict[str, Any]:
    """설계 6.1 — 출처마다 새 물품을 활성 예약과 비교한다(2026-11-15: 수집 크론 끝에서 발송 크론 앞으로).

    수집 크론과 떨어져 있어 알림 단계가 수집·보존기간 정리의 시간 예산을 쓰지 않는다(출처 독립).
    한 출처가 실패해도 다른 출처의 매칭과 발송은 계속한다.
    """
    results: dict[str, Any] = {}
    judge = matching.cron_judge()  # 2026-11-23 — Jev 제외 포함(키가 없으면 기존 판정)
    for source in SOURCE_ORDER:
        code = SOURCE_CODES[source]
        try:
            results[code] = run_match_pass(
                store, judge, item_source_code=code, deadline=deadline
            )
        except Exception as error:  # 실행 기록조차 남기지 못한 경우 — 비밀값이 섞이지 않게 형식만 남긴다
            print(f"[alerts] {code} 매칭 단계 실패: {type(error).__name__}", file=sys.stderr)
            results[code] = {"status": "failed", "error_message": type(error).__name__}
    return results


@router.get("/alert-send")
def alert_send(request: Request) -> JSONResponse:
    """설계 6.2 — 매칭 → 종료 안내 → 발송(450통 한도) → 정리. KST 11시대에 한 번 돈다."""
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
    try:
        store = SupabaseAlertStore.from_environment()
    except ValueError as error:
        # AGENTS.md 4장 — 설정이 없으면 503(입력 오류 400과 구분한다).
        return JSONResponse(status_code=503, content={"error": str(error)}, headers=no_store)
    matched = _run_alert_matching(store, deadline=started + ALERT_MATCH_DEADLINE_SECONDS)
    result = run_send_pass(
        store,
        create_email_sender(settings.mail),
        settings,
        deadline=started + ALERT_DEADLINE_SECONDS,
    )
    failed = result.get("status") == "failed" or any(
        outcome.get("status") == "failed" for outcome in matched.values()
    )
    return JSONResponse(
        status_code=500 if failed else 200,
        content={**result, "matching": matched},
        headers=no_store,
    )
