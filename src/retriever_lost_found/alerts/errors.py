from __future__ import annotations

from ..errors import safe_error

__all__ = ["AlertLimitError", "safe_error"]


class AlertLimitError(Exception):
    """예약 제한·발송 한도에 걸림 — API는 429로 돌려준다(설계 5.4)."""
