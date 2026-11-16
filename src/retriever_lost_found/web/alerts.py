from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ..alerts.errors import AlertLimitError
from ..alerts.reservations import AlertService

UNAVAILABLE = {"error": "분실물 알림 예약을 지금은 사용할 수 없습니다."}


def client_ip(request: Request) -> str:
    """설계 5.2·14장 — IP 제한에 쓸 접속 IP.

    Vercel은 들어온 X-Forwarded-For를 덮어써 위조를 막으므로 Vercel 위(`VERCEL=1`)에서만
    전달 헤더를 믿는다. 그 밖(로컬·다른 호스트)에서는 헤더를 바꿔 제한을 피할 수 있어 소켓 주소를 쓴다.
    """
    socket_ip = request.client.host if request.client else ""
    if os.getenv("VERCEL") != "1":
        return socket_ip
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded.strip():
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip", "").strip()
    return real_ip or socket_ip


async def _json_object(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except ValueError:
        raise ValueError("요청 형식이 올바르지 않습니다.") from None
    if not isinstance(payload, dict):
        raise ValueError("요청 형식이 올바르지 않습니다.")
    return payload


def create_alert_router(service: AlertService | None) -> APIRouter:
    """설계 5.4 — 예약 API. 오류는 앱의 ValueError(400)·RuntimeError(502) 처리기를 따른다."""
    router = APIRouter(prefix="/api/alerts", include_in_schema=False)

    async def run_token_action(request: Request, action: Callable[[Any], dict[str, Any]]) -> JSONResponse:
        payload = await _json_object(request)
        return JSONResponse(content=await run_in_threadpool(action, payload.get("token")))

    @router.post("")
    async def create_reservation(request: Request) -> JSONResponse:
        if service is None:
            return JSONResponse(status_code=503, content=UNAVAILABLE)
        payload = await _json_object(request)
        try:
            result = await run_in_threadpool(service.create, payload, client_ip(request))
        except AlertLimitError as error:
            return JSONResponse(status_code=429, content={"error": str(error)})
        return JSONResponse(status_code=202, content=result)

    @router.get("/summary")
    async def summary(token: str = "") -> JSONResponse:
        if service is None:
            return JSONResponse(status_code=503, content=UNAVAILABLE)
        return JSONResponse(content=await run_in_threadpool(service.summary, token))

    @router.post("/confirm")
    async def confirm(request: Request) -> JSONResponse:
        if service is None:
            return JSONResponse(status_code=503, content=UNAVAILABLE)
        return await run_token_action(request, service.confirm)

    @router.post("/extend")
    async def extend(request: Request) -> JSONResponse:
        if service is None:
            return JSONResponse(status_code=503, content=UNAVAILABLE)
        return await run_token_action(request, service.extend)

    @router.post("/cancel")
    async def cancel(request: Request) -> JSONResponse:
        if service is None:
            return JSONResponse(status_code=503, content=UNAVAILABLE)
        return await run_token_action(request, service.cancel)

    return router
