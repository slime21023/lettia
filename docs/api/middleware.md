---
title: Middleware API
---

# Middleware API

Lettia middleware is a function-composition API rather than a base class
hierarchy. This keeps middleware small and makes its nesting behavior explicit.

## Core types

```python
Handler = Callable[[Context], Awaitable[Any]]
Middleware = Callable[[Handler], Handler]
```

A middleware receives the next handler and returns a handler with the same
shape:

```python
def timing() -> Middleware:
    def middleware(next_handler: Handler) -> Handler:
        async def handler(ctx: Context) -> Any:
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

The first middleware in the list becomes the outermost wrapper. `App` uses the
function during compilation to build pre-routing, global, and route chains.

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
recover(
    on_recover: Callable[[Context, Exception], Any] | None = None,
) -> Middleware
```

Preserves `HTTPException`. Other exceptions are logged and converted to a 500
response, or passed to `on_recover` when supplied.

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
not `*`.

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

`timeout()` converts deadline expiry to HTTP 504. `body_limit()` validates
Content-Length and forces body reading with the configured limit, returning
HTTP 400 or 413 for invalid or oversized requests.

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
limit.

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

Loads a signed JSON object into `ctx.state["session"]` and writes a new cookie
when the mapping changes. The signature protects integrity; the payload is not
encrypted.
