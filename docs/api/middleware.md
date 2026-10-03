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

Answers OPTIONS requests with both Origin and Access-Control-Request-Method
before route dispatch; ordinary OPTIONS reaches routing. Credentials require
explicit origins, not `*`. An explicit origin list merges `Vary: Origin` on
every response, including missing or disallowed origins, preserving existing
tokens and `Vary: *`. Wildcard allowed headers/methods produce explicit reflected
preflight permissions with corresponding Vary fields. See the
[CORS guide](../middleware.md#cors) for the policy and examples.

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

`timeout()` converts expiry before the response-start attempt to HTTP 504.
When the handler times out, the resulting error response is sent without the
expired deadline, including with nested timeout middleware and yielding sends.
The same deadline also limits response streaming; after headers have been sent,
Lettia closes the iterator and terminates the stream because HTTP status can no
longer change. Only expiry of Lettia's deadline becomes 504; unrelated upstream
`TimeoutError` remains an application error. Expiry during a transport send
does not retry the event or send replacement headers; the iterator is closed.
`body_limit()` validates Content-Length and forces body reading with the
configured limit, returning HTTP 400 or 413 for invalid or oversized requests.
The limit is checked again even when the body was already cached.
The shared Context reader retains the strictest limit and any body rejection;
streamed error responses cannot cause the disconnect monitor to cache rejected
input. It discards remaining body events while waiting for disconnect.

### `rate_limit()`

```python
rate_limit(
    requests_per_minute: int = 60,
    key_func: Callable[[Context], str] | None = None,
) -> Middleware
```

Uses an in-memory sliding window. The default key is the ASGI client address;
missing/None clients share a fixed unknown-client bucket. Forwarded headers are
never read by the default key function. A rejected request
raises HTTP 429 and includes `Retry-After`.

`MemoryRateLimiter` is also public:

```python
MemoryRateLimiter(requests_per_minute: int = 60)
limiter.is_allowed(key: str) -> tuple[bool, int]
```

This limiter is process-local and should not be treated as a distributed rate
limit. Trusted proxy identity must be established by the server or an explicit
`key_func`; do not trust client-supplied forwarding headers.

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

Clearing a populated session expires the cookie with the same Path, Secure,
HttpOnly, and SameSite attributes used when creating it. With `https_only=True`,
this also preserves valid deletion for `__Host-` and `__Secure-` cookie names.

Within `App`, Session signing and CORS/Request ID response headers are finalized
after the entire middleware chain returns, just before the writer validates and
sends headers. Session uses the latest state, including changes by outer
middleware or error handlers. Normal, replaced, and fallback responses share
this step without rerunning request-side middleware. The writer uses a header
copy; it does not append policy cookies to the original response object.
Standalone middleware calls still decorate their returned response immediately.
