import time
from collections import deque
from collections.abc import Callable

from lettia.context import Context
from lettia.errors import abort
from lettia.middleware.base import Handler, Middleware
from lettia.response import Response


class MemoryRateLimiter:
    def __init__(self, requests_per_minute: int = 60) -> None:
        if requests_per_minute <= 0:
            raise ValueError("requests_per_minute must be greater than zero")
        self.requests_per_minute: int = requests_per_minute
        self.window_seconds: float = 60.0
        self._history: dict[str, deque[float]] = {}
        self._checks_since_cleanup = 0

    def is_allowed(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        cutoff = now - self.window_seconds

        self._checks_since_cleanup += 1
        if self._checks_since_cleanup >= 256:
            self._purge_expired(cutoff)
            self._checks_since_cleanup = 0

        if key not in self._history:
            self._history[key] = deque()

        timestamps = self._history[key]

        # Pop expired timestamps from left (O(1) per pop)
        while timestamps and timestamps[0] <= cutoff:
            timestamps.popleft()

        if len(timestamps) >= self.requests_per_minute:
            retry_after = int(self.window_seconds - (now - timestamps[0]))
            return False, max(1, retry_after)

        timestamps.append(now)
        return True, 0

    def _purge_expired(self, cutoff: float) -> None:
        expired_keys = [
            key
            for key, timestamps in self._history.items()
            if not timestamps or timestamps[-1] <= cutoff
        ]
        for key in expired_keys:
            del self._history[key]


def rate_limit(
    requests_per_minute: int = 60,
    key_func: Callable[[Context], str] | None = None,
) -> Middleware:
    limiter = MemoryRateLimiter(requests_per_minute=requests_per_minute)

    def default_key_func(ctx: Context) -> str:
        client = ctx.scope.get("client")
        if client:
            return client[0]
        return "<unknown-client>"

    get_key = key_func if key_func is not None else default_key_func

    def middleware(next_handler: Handler) -> Handler:
        async def handler(ctx: Context) -> Response:
            key = get_key(ctx)
            allowed, retry_after = limiter.is_allowed(key)

            if not allowed:
                abort(
                    429,
                    "Too Many Requests",
                    headers={"Retry-After": str(retry_after)},
                )

            return await next_handler(ctx)

        return handler

    return middleware
