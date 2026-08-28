from __future__ import annotations

import hmac
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..config import read_optional_env_value
from ..ingestion.service import run_incremental_sync


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
