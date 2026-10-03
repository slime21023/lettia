---
title: Middleware
---

# Middleware

Use middleware for behavior shared by multiple endpoints: CORS, request IDs,
logging, sessions and limits. Start with the
[built-in baseline](#recommended-baseline); write custom middleware only when
you need additional behavior. Examples assume an `app = App()` instance.

## Built-in middleware

| Middleware | Purpose | Important options |
|---|---|---|
| `recover()` | Log and re-raise errors in standalone chains | — |
| `request_logger()` | Log method, path, status, and duration | `log_func` |
| `cors()` | CORS headers and OPTIONS preflight | origins, methods, headers, credentials |
| `request_id()` | Propagate or generate `X-Request-ID` | header name, generator |
| `timeout()` | Enforce a handler and response-stream deadline | seconds |
| `body_limit()` | Enforce request body size | max bytes |
| `rate_limit()` | In-memory sliding-window limit | requests/minute, key function |
| `session()` | Signed JSON cookie session | secret, cookie name, max age |

### Recommended baseline

```python
from lettia.middleware import body_limit, cors, request_id, request_logger

app.use(
    request_id(),
    request_logger(),
    cors(allow_origins=["https://frontend.example"]),
    body_limit(max_bytes=1024 * 1024),
)
```

`App` renders errors from handlers and each middleware layer before returning
to outer middleware. Put CORS and request IDs outside middleware whose error
responses need those headers. Unexpected exceptions are logged once by App;
intentional `HTTPException` values retain their status without error logging.
Cancellation propagates. Standalone `build_chain()` keeps exception propagation;
`recover()` remains available there for logging and re-raising.

`request_logger()` measures time until the downstream handler returns or raises.
It does not measure full response transmission or consumption of a stream.
The order above gives preflight requests a Request ID and a log entry, because
both middleware run before CORS can return early.

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
from lettia import Context, Response
from lettia.middleware import Handler


def normalize_slash(next_handler: Handler) -> Handler:
    async def handler(ctx: Context) -> Response:
        if ctx.path != "/" and ctx.path.endswith("/"):
            ctx.path = ctx.path.rstrip("/")
        return await next_handler(ctx)

    return handler


app.use_pre(normalize_slash)
```

## Common configuration

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
a wildcard origin. The default origin policy is `"*"` for local or explicitly
public APIs; production browser APIs should always supply their allowed origins.
An explicit origin list merges `Origin` into existing `Vary` tokens for every
response, including requests without Origin and disallowed origins. Tokens are
deduplicated case-insensitively; `Vary: *` stays `*`.

Preflight means OPTIONS with both Origin and Access-Control-Request-Method;
ordinary OPTIONS requests reach routing. Wildcard `allow_headers` echoes the
requested header names as an explicit, deduplicated list, including Authorization,
so credentialed requests work. Wildcard `allow_methods` echoes the requested
method. Reflected fields also add Access-Control-Request-Headers or
Access-Control-Request-Method to Vary. Explicit lists retain their configured
permissions; they are not expanded to match the request.

Choose explicit origins for a known frontend. The scheme, host and port are
part of the origin: `http://localhost:3000` and `http://localhost:5173` differ.

| Browser use case | Configuration |
|---|---|
| Frontend sends JSON or an Authorization header | Allow its origin and the required request headers |
| Frontend uses Session cookies | Explicit origins and `allow_credentials=True`, plus matching frontend credentials and cookie settings |
| Frontend needs to read custom response headers | Requires `Access-Control-Expose-Headers`; `cors()` currently has no `expose_headers` option |

`allow_headers` concerns headers sent by the frontend, not response headers it
can read. CORS controls browser access to responses; it does not authenticate
requests. A disallowed origin does not imply that the route is never executed.
Put global CORS before authentication or limits if their error responses need
CORS headers. Group middleware only runs after matching a route, so it does not
provide the same preflight and unmatched-route coverage.

For a minimal policy check, save this as `test_cors.py` after installing
`lettia[testing]` and pytest:

```python
from lettia import App, Context, Response
from lettia.middleware import cors
from lettia.testing import TestClient

app = App()
app.use(cors(allow_origins=["https://frontend.example"]))


@app.get("/unavailable")
def unavailable(ctx: Context) -> Response:
    ctx.abort(404, "Item not found")


def test_preflight_and_error_headers() -> None:
    client = TestClient(app)
    origin = {"Origin": "https://frontend.example"}
    preflight = client.request(
        "OPTIONS",
        "/unavailable",
        headers={**origin, "Access-Control-Request-Method": "GET"},
    )
    missing = client.get("/unavailable", headers=origin)

    assert preflight.status_code == 204
    assert missing.status_code == 404
    for response in (preflight, missing):
        assert response.headers["access-control-allow-origin"] == origin["Origin"]
```

Run `uv run python -m pytest test_cors.py`; the test should pass. It checks HTTP
headers, not browser enforcement. Ordinary OPTIONS reaches routing, whereas
CORS preflight does not require an explicitly registered OPTIONS route.

### Rate limiting and proxy headers

The default key is the ASGI client address. If that address is missing or None,
all unknown clients share one separate bucket. The default never reads
X-Forwarded-For. Configure trusted proxies at the ASGI server, or explicitly
provide a `key_func` that uses already-verified identity. The limiter is
process-local, so use a
gateway or an external shared limiter when the application runs in more than
one process. A custom `key_func` changes identity only; it does not share the
in-memory counters between workers.

### Sessions

Session cookies are signed for integrity but not encrypted. Store identifiers
or non-sensitive preferences, never passwords or secrets. In HTTPS deployments
pass `https_only=True`; use an explicit CORS policy and separate CSRF/session
policy appropriate to the application.

The signature covers a versioned envelope, its issue time, and the session data.
`max_age` must be positive and is enforced when the server reads a cookie;
expiry is exclusive (`age < max_age`). Invalid signatures, invalid envelopes,
future issue times, expired cookies, and old unversioned cookies load an empty
session. Only modified sessions issue a new cookie and reset the issue time.
Clearing a previously populated session expires the cookie with the configured
Secure and SameSite attributes intact, including for secure cookie prefixes.

**Migration:** existing session cookies are invalidated by this format change.
Users must sign in again after upgrading.

### Response finalization

When using App, CORS, Request ID and Session headers are applied just before
the response is sent. You can update Session state after `await next_handler(ctx)`
or in an error handler; the outgoing cookie uses that latest state.
Unchanged sessions do not issue a cookie; clearing an existing session issues a
deletion cookie, while clearing a newly created session leaves it unsaved.

These policies also apply to error responses when their middleware ran. Reusing
a response does not accumulate policy cookies or mutate its original headers.
Request handlers are not rerun when the framework replaces an invalid response.

Outer middleware inspecting a returned response sees its application headers;
the registered built-in policy headers are added at the send boundary. Calling
middleware directly without `App` still returns a decorated response. A policy
that raises during finalization is omitted from this request's error response;
other registered policies still apply, and stream resources are closed.

Custom middleware continues to return a `Response` through the public interface.
Private policy registration is not an application extension point. The
[middleware reference](api/middleware.md#request-work-and-deferred-response-policies)
and [architecture diagrams](architecture.md#http-request-lifecycle) explain
internal ordering and failure handling.

## Writing custom middleware

The example below is a complete middleware definition. Register it with
`app.use(timing())` on your application. It observes handler execution only;
it does not wait for the returned response stream to be sent.

Lettia middleware is a callable that receives the next handler and returns a
handler with the same shape:

```python
import logging
from time import perf_counter

from lettia import Context, Response
from lettia.middleware import Handler, Middleware

logger = logging.getLogger(__name__)


def timing() -> Middleware:
    def middleware(next_handler: Handler) -> Handler:
        async def handler(ctx: Context) -> Response:
            started = perf_counter()
            result = await next_handler(ctx)
            duration = perf_counter() - started
            logger.info("%s handler returned in %.3fs", ctx.path, duration)
            return result

        return handler

    return middleware
```

For request validation and typed state, save this complete example as
`test_middleware.py`. It validates a caller-provided display label; that label
does not establish the caller's identity.

```python
from lettia import App, Context, Response, StateKey
from lettia.middleware import Handler
from lettia.testing import TestClient

CLIENT_LABEL = StateKey[str]("example.client_label")


def require_label(next_handler: Handler) -> Handler:
    async def handler(ctx: Context) -> Response:
        label = ctx.header("x-client-label")
        if not label:
            ctx.abort(400, "X-Client-Label is required")
        ctx.state.set(CLIENT_LABEL, label)
        return await next_handler(ctx)

    return handler


app = App()
app.use(require_label)


@app.get("/hello")
def hello(ctx: Context) -> dict[str, str]:
    return {"client": ctx.state.require(CLIENT_LABEL)}


def test_label_validation_and_state() -> None:
    client = TestClient(app)
    assert client.get("/hello").status_code == 400
    response = client.get("/hello", headers={"X-Client-Label": "demo"})
    assert response.status_code == 200
    assert response.json() == {"client": "demo"}
```

Run `uv run python -m pytest test_middleware.py`. The shared key object carries
the type from middleware to handler. Import that same key wherever it is used;
creating another `StateKey` with the same name creates a different key.

## WebSocket boundary

HTTP middleware is not applied to WebSocket scopes. Authenticate and authorize
the handshake, enforce origin policy, and set connection or message limits in
the WebSocket handler or in server-level middleware.
