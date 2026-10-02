---
title: Middleware API
---

# Middleware API

Lettia middleware is a function-composition API rather than a base class
hierarchy. This keeps middleware small and makes its nesting behavior explicit.

## Core types

```python
Handler = Callable[[Context], Awaitable[Response]]
Middleware = Callable[[Handler], Handler]
```

A middleware receives the next handler and returns a handler with the same
shape:

```python
def timing() -> Middleware:
    def middleware(next_handler: Handler) -> Handler:
        async def handler(ctx: Context) -> Response:
            started = perf_counter()
            result = await next_handler(ctx)
            logger.info("%s", perf_counter() - started)
            return result

        return handler

    return middleware
```

## `build_chain()`

```python
build_chain(handler: Handler, middlewares: list[Middleware]) -> Handler
```

The first middleware in the list becomes the outermost wrapper. Standalone
composition propagates exceptions. App uses the same nesting order, adding
error rendering at each layer so outer middleware receives error responses.

```python
app.use(first, second)
```

```text
first before → second before → handler → second after → first after
```

## Middleware scopes

| Scope | Registration | Execution point |
|---|---|---|
| Pre-routing | `app.use_pre(...)` | Before router matching |
| Global | `app.use(...)` | Around dispatch, including 404/405 |
| Route | `app.add_route(..., middlewares=...)` | After a route is selected |
| Group | `app.group(prefix, *middlewares)` | Inherited by group routes |

Pre-routing middleware can rewrite `ctx.path`. Global middleware is the right
scope for cross-cutting behavior such as recovery, CORS, request IDs, logging,
and limits.

## Built-in factories

### `recover()`

```python
recover() -> Middleware
```

Preserves `HTTPException`. Other exceptions are logged and re-raised in
standalone chains. App already renders and logs unexpected errors at each
boundary, so registering `recover()` in an App is optional.

### `request_logger()`

```python
request_logger(
    log_func: Callable[[str], None] | None = None,
) -> Middleware
```

Logs method, path, status, and elapsed time. A custom logger callback can be
used for structured logging integration.

### `cors()`

```python
cors(
    allow_origins: Sequence[str] = ("*",),
    allow_methods: Sequence[str] = (...),
    allow_headers: Sequence[str] = ("*",),
    allow_credentials: bool = False,
    max_age: int = 600,
) -> Middleware
```

Answers origin-bearing OPTIONS preflight requests before route dispatch and
adds CORS headers to normal responses. Credentials should use explicit origins,
not `*`. The default origin value is `"*"`; set explicit origins for production
browser applications. For an explicit allowed origin, existing `Vary` tokens
are merged with `Origin` case-insensitively; `*` is preserved.

### `request_id()`

```python
request_id(
    header_name: str = "x-request-id",
    generator: Callable[[], str] | None = None,
) -> Middleware
```

Reuses the incoming header or generates an ID, stores it in `ctx.state`, and
adds it to the response.

### `timeout()` and `body_limit()`

```python
timeout(seconds: float) -> Middleware
body_limit(max_bytes: int) -> Middleware
```

`timeout()` converts expiry before response headers are committed to HTTP 504.
The same deadline also limits response streaming; after headers have been sent,
Lettia closes the iterator and terminates the stream because HTTP status can no
longer change. Only expiry of Lettia's deadline becomes 504; unrelated upstream
`TimeoutError` remains an application error.
`body_limit()` validates Content-Length and forces body reading with the
configured limit, returning HTTP 400 or 413 for invalid or oversized requests.
The limit is checked again even when the body was already cached.

### `rate_limit()`

```python
rate_limit(
    requests_per_minute: int = 60,
    key_func: Callable[[Context], str] | None = None,
) -> Middleware
```

Uses an in-memory sliding window. The default key is the client address, with
`X-Forwarded-For` fallback when no ASGI client tuple exists. A rejected request
raises HTTP 429 and includes `Retry-After`.

`MemoryRateLimiter` is also public:

```python
MemoryRateLimiter(requests_per_minute: int = 60)
limiter.is_allowed(key: str) -> tuple[bool, int]
```

This limiter is process-local and should not be treated as a distributed rate
limit. Its fallback `X-Forwarded-For` key is safe only when a trusted proxy
controls that header.

### `session()`

```python
session(
    secret_key: str,
    cookie_name: str = "session",
    max_age: int = 14 * 86400,
    same_site: str = "lax",
    https_only: bool = False,
) -> Middleware
```

Loads a signed JSON object into `ctx.state` under the typed `SESSION` key and
writes a new cookie when that mapping changes. The signature protects
integrity; the payload is not encrypted. Set `https_only=True` when the cookie
is served over HTTPS. A signed issue time enforces positive `max_age` on the
server. Invalid, expired, future-dated, or old-format cookies load an empty
session. Cookies are renewed only on modification; upgrading invalidates old
sessions and requires users to sign in again.
