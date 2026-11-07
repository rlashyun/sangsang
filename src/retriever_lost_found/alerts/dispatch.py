from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from .errors import safe_error
from .links import mail_links, parse_timestamp
from .reservations import utc_now
from .sender import (
    DailyLimitExceeded,
    EmailSender,
    EmailSession,
    LoginFailed,
    OutgoingEmail,
    PermanentSendError,
    SendError,
    TemporarySendError,
)
from .settings import AlertSettings
from .store import AlertStore
from .templates import confirmation_email, expiry_notice_email, match_email

SEND_BATCH_SIZE = 20
MAX_SEND_ATTEMPTS = 3  # 설계 6.3 — 일시 오류는 세 번째 시도에서 실패로 둔다
CONFIRM_LINK_VALID_FOR = timedelta(hours=24)  # 설계 5.4 — 확인 링크 유효 시간


def compose_message(
    store: AlertStore,
    settings: AlertSettings,
    message: dict[str, Any],
    now: datetime,
) -> OutgoingEmail | None:
    """보낼 수 없는 메일(예약이 끝남·물품 없음)은 None — 호출한 쪽이 지운다."""
    payload = store.message_payload(int(message["id"]))
    if payload is None:
        return None
    reservation = payload["reservation"]
    email = reservation.get("email")
    status = reservation.get("status")
    # 설계 6.2 — 보내기 직전 상태만 다시 본다. 만료 시각은 보지 않는다(만료일 아침 매칭분을 보낸다).
    if not email or status not in ("pending", "active"):
        return None
    links = mail_links(settings, reservation, now)
    kind = message["kind"]
    if kind == "confirm":
        if status != "pending":
            return None
        # 링크가 이미 만료된 확인 메일은 보내지 않는다. 예약은 정리 단계에서 '미확인 종료'가 된다.
        if parse_timestamp(reservation["created_at"]) <= now - CONFIRM_LINK_VALID_FOR:
            return None
        return confirmation_email(email, reservation, links)
    if status != "active":
        return None
    if kind == "match":
        items = payload.get("items") or []
        return match_email(email, reservation, links, items) if items else None
    if kind == "expiry_notice":
        return expiry_notice_email(email, reservation, links)
    return None


def _release(store: AlertStore, messages: list[dict[str, Any]], counts: dict[str, int]) -> None:
    for message in messages:
        store.finish_message(int(message["id"]), "pending")
        counts["messages_deferred"] += 1


def _send_pending(
    store: AlertStore,
    sender: EmailSender,
    settings: AlertSettings,
    counts: dict[str, int],
    *,
    deadline: float,
    clock: Callable[[], float],
    now: Callable[[], datetime],
) -> str:
    session: EmailSession | None = None
    # 일시 오류 메일은 이번 실행에서 다시 집지 않게 sending으로 붙잡아 두었다가 끝에 되돌린다.
    retry_later: list[tuple[dict[str, Any], str]] = []
    try:
        while True:
            if clock() >= deadline:
                return "partial"
            batch = store.claim_messages(limit=SEND_BATCH_SIZE, daily_cap=settings.daily_send_limit)
            if not batch:
                return "succeeded"
            for index, message in enumerate(batch):
                message_id = int(message["id"])
                if clock() >= deadline:
                    _release(store, batch[index:], counts)
                    return "partial"
                email = compose_message(store, settings, message, now())
                if email is None:
                    store.delete_message(message_id)
                    continue
                if session is None:
                    try:
                        session = sender.open()
                    except LoginFailed:
                        _release(store, batch[index:], counts)
                        raise
                    except SendError:
                        # 연결 실패는 메일마다 다시 시도하지 않는다. 시도마다 최대 20초를 기다려
                        # 300초 함수 제한을 넘길 수 있기 때문이다. 남은 메일은 다음 실행에서 보낸다.
                        _release(store, batch[index:], counts)
                        return "partial"
                try:
                    session.send(email)
                except DailyLimitExceeded:
                    _release(store, batch[index:], counts)
                    return "partial"
                except PermanentSendError as error:
                    store.finish_message(message_id, "failed", str(error))
                    counts["messages_failed"] += 1
                except TemporarySendError as error:
                    retry_later.append((message, str(error)))
                    if session is not None:
                        session.close()
                        session = None
                else:
                    store.finish_message(message_id, "sent")
                    counts["messages_sent"] += 1
    finally:
        if session is not None:
            session.close()
        for message, error in retry_later:
            final = int(message["attempts"]) >= MAX_SEND_ATTEMPTS
            store.finish_message(int(message["id"]), "failed" if final else "pending", error)
            counts["messages_failed" if final else "messages_deferred"] += 1


def run_send_pass(
    store: AlertStore,
    sender: EmailSender,
    settings: AlertSettings,
    *,
    deadline: float,
    clock: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = utc_now,
) -> dict[str, Any]:
    """설계 6.2 — ① 만료 안내 넣기 → ② 발송(450통 한도) → ③ 정리."""
    run_id = store.start_run("send")
    counts = {
        "expiry_notices_queued": 0,
        "messages_sent": 0,
        "messages_deferred": 0,
        "messages_failed": 0,
        "reservations_expired": 0,
        "reservations_unconfirmed_closed": 0,
    }
    secrets = (settings.mail.password, settings.secret, store.secret_key)
    status = "succeeded"
    errors: list[str] = []
    try:
        counts["expiry_notices_queued"] = int(store.enqueue_expiry_notices())
        status = _send_pending(store, sender, settings, counts, deadline=deadline, clock=clock, now=now)
    except Exception as error:
        status = "failed"
        errors.append(safe_error(error, secrets))
    try:
        # 발송 뒤에 정리해야 만료일 아침 매칭분이 나간다(설계 5.3).
        closed = store.close_ended_reservations()
        counts["reservations_expired"] = int(closed.get("expired") or 0)
        counts["reservations_unconfirmed_closed"] = int(closed.get("unconfirmed_closed") or 0)
    except Exception as error:
        status = "failed"
        errors.append(safe_error(error, secrets))
    error_message = " | ".join(errors) or None
    store.finish_run(run_id, {"status": status, **counts, "error_message": error_message})
    return {"status": status, **counts, "error_message": error_message}
