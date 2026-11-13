"""Token bucket. Caps how fast the dialer can place calls, whatever asks it to."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class TokenBucket:
    def __init__(
        self, rate_per_s: float, burst: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        if rate_per_s <= 0 or burst < 1:
            raise ValueError("rate must be > 0 and burst >= 1")
        self.rate = rate_per_s
        self.burst = burst
        self._tokens = burst
        self._clock = clock
        self._last = clock()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self.burst, self._tokens + (now - self._last) * self.rate)
        self._last = now

    def try_take(self, n: float = 1.0) -> bool:
        with self._lock:
            self._refill()
            if self._tokens >= n:
                self._tokens -= n
                return True
            return False

    def wait_time(self, n: float = 1.0) -> float:
        with self._lock:
            self._refill()
            return max(0.0, (n - self._tokens) / self.rate)
