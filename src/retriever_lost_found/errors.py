from __future__ import annotations

from collections.abc import Iterable


def safe_error(error: BaseException, secrets: Iterable[str] = ()) -> str:
    """감사 로그용 오류 문구 — 비밀값을 가리고 1,000자로 자른다(PRD 5 보안, 3.1-8 감사 로그).

    수집(`ingestion_runs`)과 알림(`alert_runs`)이 같은 규칙으로 남기도록 한곳에 둔다.
    """
    message = f"{type(error).__name__}: {error}"
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[REDACTED]")
    return message[:1000]
