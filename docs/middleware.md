---
title: Middleware
---

# Middleware

Lettia middleware is a callable that receives the next handler and returns a
handler with the same shape:

```python
import logging
from time import perf_counter
from typing import Any

from lettia import Context
from lettia.middleware import Handler, Middleware

logger = logging.getLogger(__name__)


def timing() -> Middleware:
    def middleware(next_handler: Handler) -> Handler:
        async def handler(ctx: Context) -> Any:
            started = perf_counter()
            result = await next_handler(ctx)
            duration = perf_counter() - started
            logger.info("%s completed in %.3fs", ctx.path, duration)
            return result

        return handler

    return middleware
```

## Execution order

There are three HTTP middleware scopes:

| Scope | Registration | Runs |
|---|---|---|
| Pre-routing | `app.use_pre(...)` | Before route matching; can normalize `ctx.path` |
| Global | `app.use(...)` | Around dispatch, including 404, 405, and CORS preflight |
| Route/group | `app.add_route(..., middlewares=...)` or `app.group(...)` | After a route is selected |

Within each scope, the first middleware passed is the outermost wrapper:

```python
app.use(first, second)
```

```text
first before -> second before -> route -> second after -> first after
```

Use pre-routing middleware for path normalization or early rejection:

```python
def normalize_slash(next_handler: Handler) -> Handler:
    async def handler(ctx: Context) -> Any:
        if ctx.path != "/" and ctx.path.endswith("/"):
            ctx.path = ctx.path.rstrip("/")
        return await next_handler(ctx)

    return handler


app.use_pre(normalize_slash)
```

## Built-in middleware

| Middleware | Purpose | Important options |
|---|---|---|
| `recover()` | Convert unexpected exceptions to HTTP 500 | `on_recover` callback |
| `request_logger()` | Log method, path, status, and duration | `log_func` |
| `cors()` | CORS headers and OPTIONS preflight | origins, methods, headers, credentials |
| `request_id()` | Propagate or generate `X-Request-ID` | header name, generator |
| `timeout()` | Return HTTP 504 after a deadline | seconds |
| `body_limit()` | Enforce request body size | max bytes |
| `rate_limit()` | In-memory sliding-window limit | requests/minute, key function |
| `session()` | Signed JSON cookie session | secret, cookie name, max age |

### Recommended baseline

```python
from lettia.middleware import body_limit, cors, recover, request_id, request_logger

app.use(
    recover(),
    request_logger(),
    cors(allow_origins=["https://frontend.example"]),
    request_id(),
    body_limit(max_bytes=1024 * 1024),
)
```

`recover()` preserves intentional `HTTPException` status codes and handles
unexpected exceptions. Place it outermost when it should protect the entire
HTTP chain.

### CORS

Because global middleware wraps dispatch, `cors()` can answer an OPTIONS
preflight even when no OPTIONS route is registered:

```python
app.use(
    cors(
        allow_origins=["https://frontend.example"],
        allow_methods=["GET", "POST"],
        allow_headers=["content-type", "authorization"],
    )
)
```

Only enable credentials with explicit origins; do not combine credentials with
a wildcard origin.

### Rate limiting and proxy headers

The default key is the client address. `X-Forwarded-For` is only trustworthy
when requests come through a configured, trusted proxy. Otherwise clients can
spoof the value and bypass limits.

### Sessions

Session cookies are signed for integrity but not encrypted. Store identifiers
or non-sensitive preferences, never passwords or secrets.
