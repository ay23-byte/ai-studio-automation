"""Bounded retries with exponential backoff for free providers and tools."""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3
    base_delay: float = 0.25
    max_delay: float = 4.0
    jitter: bool = True

    def delay(self, attempt: int) -> float:
        raw = min(self.max_delay, self.base_delay * (2 ** max(0, attempt - 1)))
        return raw * (0.75 + random.random() * 0.5) if self.jitter else raw


def retry(operation: Callable[[], T], *, policy: RetryPolicy | None = None,
          exceptions: tuple[type[BaseException], ...] = (Exception,),
          logger=None, event: str = "retry") -> T:
    """Call ``operation`` up to ``policy.attempts`` times; re-raise the last error."""
    policy = policy or RetryPolicy()
    if policy.attempts < 1:
        raise ValueError("attempts must be at least 1")
    last: BaseException | None = None
    for attempt in range(1, policy.attempts + 1):
        try:
            return operation()
        except exceptions as exc:  # noqa: BLE001 - the caller declares which failures are retryable
            last = exc
            if attempt == policy.attempts:
                break
            delay = policy.delay(attempt)
            if logger:
                logger.warning(event, attempt=attempt, max_attempts=policy.attempts,
                               delay_seconds=round(delay, 3), error=str(exc)[:300])
            time.sleep(delay)
    assert last is not None
    raise last
