from __future__ import annotations

import hmac
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse

from ..config import read_optional_env_value
from ..ingestion.service import run_incremental_sync
from ..embedding.orchestration import dispatch_embeddings, process_pending_embeddings


router = APIRouter(prefix="/api/cron", include_in_schema=False)


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


def _run_source(request: Request, source: Literal["partner", "police"]) -> JSONResponse:
    authorization_error = _authorized(request)
    if authorization_error is not None:
        return authorization_error
    result = run_incremental_sync(source)
    # The DB completion trigger provides the two-source barrier. Lost HTTP
    # delivery leaves a pending job for the 11:00 KST recovery cron (PRD 4.5).
    if result.get("status") != "already_running":
        dispatch_embeddings()
    return JSONResponse(
        status_code=200 if result.get("status") == "succeeded" else 500,
        content=result,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/partner-sync")
def partner_sync(request: Request) -> JSONResponse:
    """완료된 어제까지 연계기관 등록분을 증분 동기화합니다."""
    return _run_source(request, "partner")


@router.get("/police-sync")
def police_sync(request: Request) -> JSONResponse:
    """완료된 어제까지 경찰관서 등록분을 증분 동기화합니다."""
    return _run_source(request, "police")


@router.post("/embedding-sync")
def embedding_callback(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    authorization_error = _authorized(request)
    if authorization_error is not None:
        return authorization_error
    background_tasks.add_task(process_pending_embeddings)
    return JSONResponse(status_code=202, content={"status": "accepted"},
                        headers={"Cache-Control": "no-store"})


@router.get("/embedding-sync")
def embedding_recovery(request: Request) -> JSONResponse:
    authorization_error = _authorized(request)
    if authorization_error is not None:
        return authorization_error
    result = process_pending_embeddings(recover=True)
    return JSONResponse(status_code=200 if result.get("status") in
                        ("succeeded", "pending", "waiting_or_idle") else 500,
                        content=result, headers={"Cache-Control": "no-store"})
