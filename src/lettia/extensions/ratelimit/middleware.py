"""Single-process sliding-window rate limiting as a middleware factory."""

import hashlib
import math
import re
import time
from collections import OrderedDict, deque
from collections.abc import Callable

from lettia.core import (
    Context,
    Handler,
    Middleware,
    RequestHead,
    Response,
    problem_response,
)

_RULE = re.compile(r"([1-9][0-9]*)/(second|minute|hour|day)")
_WINDOWS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}
_MAX_RAW_IDENTITY = 128
_UNKNOWN = "unknown"


def client_ip(head: RequestHead) -> str:
    """The direct peer address; never read from forwarded headers."""
    client = head.client
    if client is None or not client[0]:
        return _UNKNOWN
    return client[0]


def _identity(value: object) -> str | bytes:
    if value is None or (isinstance(value, str) and not value):
        return _UNKNOWN
    if not isinstance(value, str):
        raise TypeError(
            f"rate limit key must return str or None, got {type(value).__name__}"
        )
    if len(value) <= _MAX_RAW_IDENTITY:
        return value
    encoded = value.encode("utf-8", "surrogatepass")
    return hashlib.blake2b(encoded, digest_size=16).digest()


def _retry_after(moment: float, now: float) -> str:
    return str(max(1, math.ceil(moment - now)))


def rate_limit(
    rule: str,
    *,
    key: Callable[[RequestHead], str | None] = client_ip,
    max_keys: int = 10_000,
    clock: Callable[[], float] = time.monotonic,
) -> Middleware:
    """Allow ``limit`` requests per window, e.g. ``"5/minute"``.

    The count lives in this process's memory only: it is not shared between
    workers and is lost on restart. One factory call is one shared quota; make
    another call for an independent one. The quota is taken before the handler
    runs, so requests answered with 4xx/5xx count too. When ``max_keys``
    identities are tracked and none can be dropped, new identities get 503 and
    tracked ones stay limited.
    """
    match = _RULE.fullmatch(rule) if type(rule) is str else None
    if match is None:
        raise ValueError(f"invalid rate limit rule: {rule!r}")
    limit = int(match.group(1))
    window = _WINDOWS[match.group(2)]
    if type(max_keys) is not int or max_keys < 1:
        raise ValueError("max_keys must be a positive int")
    buckets: OrderedDict[str | bytes, deque[float]] = OrderedDict()
    limit_header = str(limit)

    def middleware(next_handler: Handler) -> Handler:
        async def run(c: Context) -> Response:
            now = clock()
            identity = _identity(key(c.request))
            cutoff = now - window
            # Ordered by last admission: only the front can be fully expired.
            while buckets:
                oldest = next(iter(buckets.values()))
                if oldest and oldest[-1] > cutoff:
                    break
                buckets.popitem(last=False)
            bucket: deque[float] | None = buckets.get(identity)
            if bucket is None:
                if len(buckets) >= max_keys:
                    free_at = next(iter(buckets.values()))[-1] + window
                    return problem_response(
                        503,
                        "Service Unavailable",
                        "rate limiter capacity exhausted",
                        headers={"Retry-After": _retry_after(free_at, now)},
                    )
                bucket = deque[float]()
            else:
                while bucket and bucket[0] <= cutoff:
                    bucket.popleft()
                if len(bucket) >= limit:
                    return problem_response(
                        429,
                        "Too Many Requests",
                        headers={
                            "Retry-After": _retry_after(bucket[0] + window, now),
                            "X-RateLimit-Limit": limit_header,
                            "X-RateLimit-Remaining": "0",
                        },
                    )
            bucket.append(now)
            buckets[identity] = bucket
            buckets.move_to_end(identity)
            remaining = str(limit - len(bucket))
            response = await next_handler(c)
            response.headers.set("X-RateLimit-Limit", limit_header)
            response.headers.set("X-RateLimit-Remaining", remaining)
            return response

        return run

    return middleware
