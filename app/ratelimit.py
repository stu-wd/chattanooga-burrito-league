"""Process-wide sliding-window rate limiter for Strava's app-wide quotas.

Every Strava HTTP call acquires here first, so the single worker never exceeds
the 15-min or daily budget. In-process only — correct for one worker process;
a horizontally-scaled deployment would move this state to Redis/DB."""

import threading
import time
from collections import deque

from .config import settings


class RateLimiter:
    def __init__(self, limits: list[tuple[int, float]]):
        # limits: list of (max_calls, window_seconds)
        self._limits = limits
        self._calls: list[deque[float]] = [deque() for _ in limits]
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block until a call is permitted under every window, then record it."""
        while True:
            with self._lock:
                now = time.monotonic()
                wait = 0.0
                for (max_calls, window), dq in zip(self._limits, self._calls):
                    while dq and now - dq[0] >= window:
                        dq.popleft()
                    if len(dq) >= max_calls:
                        wait = max(wait, window - (now - dq[0]))
                if wait <= 0:
                    for dq in self._calls:
                        dq.append(now)
                    return
            time.sleep(min(wait, 5.0) + 0.01)


limiter = RateLimiter(
    [
        (settings.strava_rate_15min, 15 * 60),
        (settings.strava_rate_daily, 24 * 60 * 60),
    ]
)
