"""PRD 5 — share a monotonic time budget across requests and retry delays."""

from __future__ import annotations

import time


class DeadlineExceeded(TimeoutError):
    """A bounded sync stage ran out of time; stop retrying and preserve data."""


def request_timeout(deadline: float | None, maximum: float) -> float:
    if deadline is None:
        return maximum
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DeadlineExceeded("동기화 단계의 제한 시간을 초과했습니다.")
    return min(maximum, remaining)


def check_retry_delay(deadline: float | None, delay: float) -> None:
    if deadline is not None and request_timeout(deadline, float('inf')) <= delay:
        raise DeadlineExceeded("재시도할 시간이 부족하여 다음 동기화로 넘깁니다.")
