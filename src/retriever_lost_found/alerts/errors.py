from __future__ import annotations

from collections.abc import Iterable


class AlertLimitError(Exception):
    """예약 제한·발송 한도에 걸림 — API는 429로 돌려준다(설계 5.4)."""


def safe_error(error: BaseException, secrets: Iterable[str] = ()) -> str:
    """오류 문구에서 비밀값을 가리고 1,000자로 자른다(설계 8장)."""
    message = f"{type(error).__name__}: {error}"
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return message[:1000]
