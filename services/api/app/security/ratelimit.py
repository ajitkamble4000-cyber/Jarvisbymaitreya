"""In-process sliding-window rate limiter.

Adequate for a single API instance. Multi-instance deployments should move the
counters to a shared store (for example Redis) behind the same ``hit`` interface.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from threading import Lock

_MAX_TRACKED_KEYS = 50_000
# When the table is over the cap, the least recently used keys are dropped down to this size. Dropping a batch
# keeps the O(n) rebuild rare; a rebuild on every call would slow every rate-limited request.
_EVICT_TARGET_KEYS = 40_000
# Expired keys are swept at most this often, not on every hit.
_SWEEP_INTERVAL_SECONDS = 60.0


class RateLimiter:
    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        self._hits: dict[str, deque[float]] = {}
        self._lock = Lock()
        self._retention = 0.0  # longest window seen; a sweep keeps every key that is still inside it
        self._next_sweep = 0.0

    def hit(self, key: str, limit: int, window_seconds: float = 60.0) -> bool:
        """Record a hit. Returns False when the key has exceeded ``limit`` in the window."""
        now = self._clock()
        cutoff = now - window_seconds
        with self._lock:
            self._retention = max(self._retention, window_seconds)
            if now >= self._next_sweep or len(self._hits) > _MAX_TRACKED_KEYS:
                self._sweep(now)
            bucket = self._hits.setdefault(key, deque())
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                return False
            bucket.append(now)
            return True

    def _sweep(self, now: float) -> None:
        """Drop keys with no hit inside the retention window. Caller holds the lock."""
        self._next_sweep = now + _SWEEP_INTERVAL_SECONDS
        cutoff = now - self._retention
        self._hits = {k: v for k, v in self._hits.items() if v and v[-1] >= cutoff}
        if len(self._hits) > _MAX_TRACKED_KEYS:
            # Every remaining key is still inside its window. Keep the most recently used keys only.
            newest = sorted(self._hits.items(), key=lambda item: item[1][-1], reverse=True)
            self._hits = dict(newest[:_EVICT_TARGET_KEYS])

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
            self._next_sweep = 0.0
