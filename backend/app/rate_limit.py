"""Lightweight, dependency-free, in-process rate limiting.

**Known limitation (documented, not hidden — see docs/security.md):** this
is a single-process, in-memory sliding-window limiter. It is correct for
the current architecture (one FastAPI process, one SQLite file) but is
NOT distributed: running multiple worker processes/machines would give
each its own independent limit (an attacker split across N workers gets
N× the allowance), and a process restart resets all counters to zero. A
real multi-instance deployment would need a shared store (e.g. Redis) —
explicitly out of scope for Phase 9 (no Redis infrastructure is added
here).

Keys client requests by `request.client.host` (the ASGI-observed TCP
source address) — never by any client-supplied header — so a visitor
cannot claim a different identity to dodge their own limit. Behind a
reverse proxy, this would need to read a *trusted* forwarded-for header
instead; that's a deployment-time (Phase 10) concern, not something this
module guesses at.
"""

import time
from collections import defaultdict, deque
from threading import Lock


class InMemoryRateLimiter:
    """Sliding-window limiter: at most `max_requests` per `window_seconds` per key."""

    def __init__(self, max_requests: int, window_seconds: float, time_func=time.monotonic):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._time_func = time_func
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        """Record one attempt for `key` and return whether it's within the limit.

        Uses only server-side monotonic time — never a client-supplied
        timestamp — so a visitor cannot manipulate their own window by
        sending a fabricated `Date`/timestamp value.
        """
        now = self._time_func()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= self.window_seconds:
                hits.popleft()
            if len(hits) >= self.max_requests:
                return False
            hits.append(now)
            return True

    def reset(self) -> None:
        """Clear all recorded hits. Used by the test suite for isolation between tests."""
        with self._lock:
            self._hits.clear()
