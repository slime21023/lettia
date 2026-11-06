# Core layer

The core layer (`lettia.core`, `lettia.middleware`) is a small ASGI framework on its own: an `App` with a router, a `Request`, response helpers, middleware, RFC 9457 error responses and lifespan management. It does not know about contracts, decoding, guards or OpenAPI. `import lettia.core` does not import the contract layer.

Everything here is imported from `lettia.core`; the built-in middleware from `lettia.middleware`. Note that `lettia.App` (contract layer) is a different class from `lettia.core.App`.

```python
from lettia.core import App, Context, Response

async def hello(c: Context) -> Response:
    return c.text(200, "hello")

app = App()
app.get("/", hello)
```

A handler is `Callable[[Context], Awaitable[Response]]` (`lettia.core.Handler`). It must return a `Response`; there is no implicit conversion of dicts or strings.

## App

```python
App(
    *,
    debug: bool = False,
    error_handler: ErrorHandler | None = None,
    context_class: type[Context] = Context,
    max_body_size: int | None = 5 * 1024 * 1024,
    allowed_hosts: Sequence[str] | None = None,
)
```

| Option | Default | Meaning |
|---|---|---|
| `debug` | `False` | When true, the default error handler puts `"<ExceptionType>: <message>"` in `detail` of 500 responses. Exposed as `app.debug`. |
| `error_handler` | default handler | `async (Context, Exception) -> Response`. Replaces the default handler (see [Errors](#errors)). When given, `debug` no longer affects error rendering. |
| `context_class` | `Context` | Class instantiated per request as `context_class(request)`. |
| `max_body_size` | 5 MiB (5242880) | Largest request body in bytes. `None` disables the limit. |
| `allowed_hosts` | `None` | Host names the app answers to, for [`request.host`, `url` and `base_url`](#url-scheme-and-host). `None` checks nothing. An invalid pattern or an empty list is a `ValueError`. |

An `App` is an ASGI 3 callable: `await app(scope, receive, send)`. It handles `http`, `websocket` and `lifespan` scopes; any other scope type raises `NotImplementedError`. WebSocket handling is described in [websocket.md](websocket.md).

### Registering routes

| Method | Meaning |
|---|---|
| `app.get(path, handler, *, middleware=())` | `route("GET", ...)` |
| `app.post`, `put`, `patch`, `delete`, `options` | Same, for the respective method. |
| `app.route(method, path, handler, *, middleware=())` | Generic form. `method` must be ASCII letters only; it is upper-cased. |
| `app.websocket(path, handler)` | See [websocket.md](websocket.md). |
| `app.group(prefix, *middleware)` | Returns a `Group` (see below). |

`middleware` is a sequence (not a single callable) that wraps this one route, for example `app.post("/login", login, middleware=[rate_limit("5/minute")])`. It exists on `App` and on every `Group`; websocket routes take none.

All registration methods return `None`. There is no decorator form and no `head` shortcut.

Registration is only allowed until the app is built (see [Lifecycle](#lifecycle)). Calling `route`, `use`, `group`, `on_startup`, `on_shutdown`, `lifespan` or similar after a successful build raises `RuntimeError("the app is already built; register before it starts")`.

### Path templates

A path must start with `/`. Segments are separated by `/`.

| Segment | Meaning |
|---|---|
| `users` | Static text. |
| `{name}` | Captures exactly one non-empty segment. `name` must be a valid Python identifier and unique within the path. Available as `c.path_params["name"]` (always `str`). |
| `*` | Wildcard; only allowed as the last segment. Captures the rest of the path, including slashes, under the key `"*"`. |

A segment may not mix text with `{...}` or `*` (for example `/a{id}` or `/*x`). Invalid templates are reported at build time as startup problems (see [Startup checks](#startup-checks)).

```python
async def user(c: Context) -> Response:
    return c.json(200, {"id": c.path_params["id"]})

async def files(c: Context) -> Response:
    return c.json(200, {"rest": c.path_params["*"]})

app.get("/users/{id}", user)
app.get("/files/*", files)
```

Behavior verified against the router:

- `GET /users/a%2Fb` gives `id == "a/b"`: the path is split on the raw (still percent-encoded) path first, then parameter values are percent-decoded. An encoded `%2F` never becomes a segment boundary.
- `GET /files/a/b%20c` gives `"*" == "a/b c"`.
- `GET /files/` matches `/files/*` with `"*" == ""`; `GET /files` does not match (404).
- An empty segment never matches `{name}`.

### Matching

Priority is static, then `{param}`, then `*`, with backtracking: a request goes to the most specific route that can serve it. With `/users/me` and `/users/{id}` registered, `/users/me` hits the static route and `/users/42` the parameter route.

- Two routes with the same method and the same shape conflict. `{a}` and `{b}` at the same position are the same shape. Conflicts are reported at build time.
- A path that exists for other methods yields `405 Method Not Allowed` with an `Allow` header listing the methods sorted alphabetically, with `HEAD` added when `GET` is registered (for example `Allow: GET, HEAD`).
- `HEAD` falls back to the `GET` handler when no `HEAD` route exists. The body is not sent, but headers (including `content-length` for byte bodies) are.
- An unknown path yields `404 Not Found`.
- 404 and 405 are raised as `HTTPError` inside the normal middleware chain (app middleware only), so `request_id`, `cors` and `access_log` registered with `app.use` see and decorate them. Group middleware does not run for 404/405.
- If the ASGI scope has no `raw_path`, the router rebuilds one by percent-encoding `scope["path"]`; an encoded slash already decoded by the server cannot be told from a real one.

### Groups

`group(prefix, *middleware)` returns a `Group` with the same `get/post/put/patch/delete/options/route/websocket/group` methods. The prefix must start with `/` and must not end with `/` (otherwise `ValueError`). Paths registered on a group must start with `/`; a bad path is recorded as a startup problem.

```python
api = app.group("/api/v1", auth_middleware)
api.get("/users/{id}", user)       # GET /api/v1/users/{id}
admin = api.group("/admin")        # prefix /api/v1/admin, inherits auth_middleware
admin.get("/stats", stats)
```

Nested groups concatenate prefixes and accumulate middleware (parent first). `group.prefix` returns the full prefix. WebSocket routes registered on a group take the prefix but not the group middleware.

Group middleware belongs to the routes registered through that group, not to a URL prefix:

- A route registered with `app.get("/api/v1/x", ...)` is not wrapped by the middleware of `app.group("/api/v1", ...)`, however similar its path is.
- Group middleware does not run for 404 or 405 responses and never for WebSocket routes. A group's authentication middleware therefore does not answer unknown paths or wrong methods under its prefix: an unauthenticated `GET /api/v1/nope` gets `404` and `POST` to a `GET`-only path gets `405` with `Allow`, which reveals which paths exist.
- If a check must cover those responses (or cross-origin handling such as `cors`), register it with `app.use`. For WebSocket routes use a contract guard or check inside the handler before `ws.accept()` (see [websocket.md](websocket.md#security)); neither `cors` nor group middleware protects a handshake.

## Context

`Context` is created per request and passed to the handler and every middleware.

| Member | Meaning |
|---|---|
| `c.request` | The `Request`. |
| `c.path_params` | `dict[str, str]` of captured parameters; assigned before the matched middleware chain runs, so middleware can read it on matched routes. Empty otherwise. |
| `c.state` | `dict[object, object]`, created lazily. Backing store for `Key`; prefer `Key`. |

Response helpers (all return a new `Response`):

| Method | Result |
|---|---|
| `c.text(status, text)` | `text.encode()` body (UTF-8), `content-type: text/plain; charset=utf-8`. |
| `c.json(status, data)` | `msgspec.json` encoding of `data`, `content-type: application/json`. |
| `c.no_content()` | `Response(204)`, no body. |
| `c.redirect(location, status=302)` | Empty response with a `location` header. `status` is 301, 302, 303, 307 or 308 (`ValueError` otherwise, as for an empty location or one with a line break). The location is sent as given: encode non-ASCII characters first. |
| `c.bytes(status, data, media_type="application/octet-stream")` | Raw bytes with the given `content-type`. |
| `c.stream(status, chunks, media_type)` | `chunks` is an `AsyncIterator[bytes]`; `content-type` set to `media_type`. |

`Response(status=200, body=None, headers=None, *, after_send=None, watch_disconnect=False)`: `status` must be in 100..599 (`ValueError` otherwise); `body` is `bytes`, an `AsyncIterator[bytes]` or `None`; `headers` is a `MutableHeaders`; `after_send` is an optional callback, see [After the response is sent](#after-the-response-is-sent); `watch_disconnect` opts a streamed body into [disconnect monitoring](#watching-for-a-disconnect). Attributes `status`, `body`, `headers`, `after_send` and `watch_disconnect` are plain and mutable.

### Cookies

```python
response.set_cookie("theme", "dark", max_age=3600, secure=True)
response.delete_cookie("theme")
```

`response.set_cookie(name, value, *, max_age=None, expires=None, path="/", domain=None, secure=False, httponly=True, samesite="lax")` appends one `Set-Cookie` header and keeps the cookies already set. `HttpOnly` and `SameSite=Lax` are on by default; pass `secure=True` outside local development (`samesite="none"` requires it, and `samesite=None` omits the attribute). `expires` must be a timezone-aware `datetime`. The value is written as given, so it must consist of cookie-safe characters (no whitespace, quotes, commas, semicolons or backslashes): encode anything else, for example with `urllib.parse.quote`. A name, value, path or domain that is invalid, a name that reads like an attribute (`Path`, `Secure`), a `__Secure-`/`__Host-` name without the attributes it demands, and a cookie over 4096 bytes raise `ValueError` and add nothing.

`response.delete_cookie(name, *, path="/", domain=None, secure=False, httponly=True, samesite="lax")` sends an expired empty cookie. `path` and `domain` must match the ones the cookie was set with, or the browser keeps it.

Serialization by the ASGI adapter:

- For `bytes`/`None` bodies, `content-length` is added unless the response already has one, or the status is 1xx/204/304.
- For `HEAD` requests and statuses 1xx/204/304 no body bytes are sent; a streamed body is not iterated, but is still closed (`aclose()`).
- Streamed bodies are sent chunk by chunk and `aclose()`d afterwards, also when sending the response start fails. No `content-length` is added. An ordinary streamed response does not watch `receive`: a client that leaves is noticed only when a later `send` fails.

```python
async def events(c: Context) -> Response:
    async def gen():
        for i in range(3):
            yield f"{i}\n".encode()
    return c.stream(200, gen(), "text/plain")

async def download(c: Context) -> Response:
    response = c.bytes(200, b"...", "application/pdf")
    response.headers.set("content-disposition", 'attachment; filename="a.pdf"')
    return response
```

#### Watching for a disconnect

`Response(..., watch_disconnect=True)` makes the ASGI adapter watch for the client leaving while a streamed body is sent. Core does not know what the stream is (an SSE extension sets the flag); the flag has no effect on `bytes`/`None` bodies, `HEAD` or 1xx/204/304 responses, which are sent as before.

- The adapter owns `receive` for the whole response. Before the response starts it releases the request body: the unread remainder is dropped, not buffered. A body already read stays available through `body()`; an unread `body()`/`stream()`, or one more chunk from a `stream()` iterator obtained earlier, raises `RuntimeError`. Monitored responses therefore do not support full-duplex request/response.
- If another coroutine is still reading the request body when the handler returns, releasing fails with `RuntimeError` before the response starts and the response body is closed. The error reaches the server (it is raised in the ASGI adapter, outside the handler's error conversion), not the client as a problem document.
- When the client disconnects before the final body message is being sent, the stream is cancelled and closed and `ClientDisconnect` is raised (the request is dropped, `access_log` records 499). `after_send` is not called. If the disconnect is reported while the final message is already being sent, the adapter waits for that `send` to return and treats a successful send as complete.
- An error in `receive` is not treated as a disconnect: it propagates after the stream is closed. An error raised by the stream wins over a simultaneous disconnect. Cancelling the request cancels the monitoring tasks and propagates `CancelledError`.

#### After the response is sent

`response.after_send` is a synchronous `Callable[[], None]`. The ASGI adapter calls it once, after the last body message was sent and a streamed body was closed (also for `HEAD`, 204 and 304). It is meant for cheap, non-blocking notification, for example scheduling a background job; it must not `await` or do slow work.

- It is not called when the handler, the send or the stream raises, when the request is cancelled, or when a [monitored response](#watching-for-a-disconnect) loses its client before the final message. "Sent" means the ASGI `send` calls returned; it does not prove that the client received the response.
- A callback that raises `Exception` is logged on the `lettia` logger and does not change the response that was already sent. A `BaseException` (for example `CancelledError`) propagates.
- The callback belongs to the response object that is actually sent. A middleware that returns a different response drops the callback of the one it received.
- A middleware that sets `after_send` must wrap the existing callback and call it, not overwrite it. The inner middleware's callback runs first.
- WebSocket handshake denials do not use `after_send`.

### Key

`Key[T](name)` is a typed slot in `c.state`. Keys compare by identity, so two keys with the same name do not collide.

| Method | Meaning |
|---|---|
| `key.set(c, value)` | Store `value`. |
| `key.get(c)` | Return the value; raises `LookupError` when unset. |
| `key.get(c, default)` | Return `default` when unset. |

```python
from lettia.core import Key

User = Key[str]("user")

def authenticate(next_):
    async def run(c):
        User.set(c, "alice")
        return await next_(c)
    return run

async def me(c):
    return c.text(200, User.get(c))
```

## Request

`c.request` is a `Request`, a subclass of `RequestHead`. `RequestHead` is the same object without body access (this is what guards receive in the contract layer).

| Attribute | Type | Meaning |
|---|---|---|
| `method` | `str` | As given by the server (not upper-cased by Core). |
| `path` | `str` | Decoded path. |
| `raw_path` | `bytes` | Raw path as sent, without query string. |
| `loggable_path` | `str` | `raw_path` decoded as latin-1: still percent-encoded, safe to log. |
| `client` | `tuple[str, int] \| None` | From the ASGI scope. |
| `headers` | `Headers` | Parsed lazily, cached. |
| `query` | `QueryParams` | Parsed lazily, cached. |
| `cookies` | `Mapping[str, str]` | Parsed lazily from all `Cookie` headers, cached. |
| `scheme` | `str` | `http` or `https` (`ws` / `wss` for a WebSocket guard), from the ASGI scope. |
| `host` | `str` | The `Host` header, see [URL, scheme and host](#url-scheme-and-host). |
| `base_url`, `url` | `str` | `{scheme}://{host}/` and the full requested URL. |

```python
async def info(c: Context) -> Response:
    r = c.request
    return c.json(200, {
        "page": r.query.get("page", "1"),
        "tags": r.query.get_all("tag"),
        "session": r.cookies.get("session"),
        "agent": r.headers.get("user-agent"),
    })
```

### Body

```python
body = await c.request.body()         # whole body as bytes, cached
async for chunk in c.request.stream():  # chunks, empty chunks skipped
    ...
```

- `body()` and `stream()` are mutually exclusive. `stream()` after `body()` raises `RuntimeError`, as does `body()` after `stream()`, a second `stream()` call, or `body()` after an earlier `body()` that failed. Repeated successful `body()` calls return the cached bytes. After the ASGI adapter released the body for a [monitored response](#watching-for-a-disconnect), only the cached bytes can still be read.
- Size limit: if `Content-Length` is present and larger than `max_body_size`, `HTTPError(413, "Payload Too Large", "body exceeds N bytes")` is raised before reading. Chunks are also counted while reading, so a missing or lying `Content-Length` is still caught. For `stream()` the declared-length check happens when `stream()` is called; the running count is checked during iteration.
- A `Content-Length` that is not ASCII digits raises `HTTPError(400, "Bad Request", "invalid Content-Length header")`.
- `ClientDisconnect` is raised while reading if the client disconnects. `App` does not convert it: the request is dropped without a response, and `access_log` records it as status 499.

### Headers

`Headers` (`request.headers`) is a read-only `Mapping[str, str]`. Names are case-insensitive; values are decoded as latin-1.

| Member | Meaning |
|---|---|
| `h["name"]`, `h.get("name", default=None)` | First value; `KeyError` / default when absent. |
| `h.get_all("name")` | List of all values, in order. |
| `h.multi_items()` | All `(name, value)` pairs, including repeats. |
| `"name" in h`, `len(h)`, iteration | Unique names (lowercase) only. |
| `Headers.from_pairs(pairs)` | Build from `(str, str)` pairs. |

`MutableHeaders` (`response.headers`) adds:

| Member | Meaning |
|---|---|
| `set(name, value)` | Replace all existing values of `name`. |
| `add(name, value)` | Append a value, keep existing ones (use for `set-cookie`). |
| `remove(name)` | Delete all values of `name`. |
| `raw` | The list of `(bytes, bytes)` pairs sent by ASGI. |

Names must be HTTP tokens and values must not contain `\r` or `\n`; otherwise `set`/`add` raise `ValueError`. Values are encoded as latin-1. Names are stored lowercase.

### Query parameters

`QueryParams` is a read-only `Mapping[str, str]`: `q["a"]` and `q.get("a")` return the first value; `q.get_all("a")` returns a list (empty when absent). Blank values are kept (`?a=` gives `""`). Percent-decoding and `+` as space follow `urllib.parse.parse_qsl`.

### Cookies

`request.cookies` is a plain dict. All `Cookie` headers are split on `;`; pieces without `=` or without a name are ignored; surrounding double quotes on a value are removed; the first value of a repeated name wins. Cookie values are not percent-decoded.

### URL, scheme and host

```python
async def where(c: Context) -> Response:
    r = c.request
    return c.json(200, {"scheme": r.scheme, "host": r.host, "url": r.url})
# GET https://api.example.com:8443/items/a%2Fb?page=2
# {"scheme": "https", "host": "api.example.com:8443",
#  "url": "https://api.example.com:8443/items/a%2Fb?page=2"}
```

| Attribute | Value |
|---|---|
| `scheme` | The scope's `scheme`; `http` when it has none. |
| `host` | The `Host` header in lower case, port included when sent. A missing or malformed header (spaces, `/`, `@`, a bad port) falls back to the address the server listens on, written without the port when it is the default of the scheme; a Unix socket or an unknown address gives `localhost`. |
| `base_url` | `{scheme}://{host}/`. |
| `url` | `{scheme}://{host}`, the raw path exactly as received (`%2F` stays encoded) and `?` plus the query string when there is one. |

`root_path` is not part of any of them, and there is no `url_for`.

**Behind a proxy.** Lettia never reads `Forwarded` or `X-Forwarded-*`. `scheme` and `client` come from the ASGI scope, so let the server rewrite the scope for a proxy you trust:

- uvicorn does it by default for `X-Forwarded-Proto` and `X-Forwarded-For`, but only when the connection comes from `127.0.0.1` or `::1`; name your proxy with `--forwarded-allow-ips` (or `FORWARDED_ALLOW_IPS`). It rewrites only `client` and `scheme`: `Host` and `X-Forwarded-Host` are left alone, so make the proxy pass the public `Host` through.
- hypercorn needs `hypercorn.middleware.ProxyFixMiddleware` around the app, with `trusted_hops` set to the number of proxies in front. It also takes `X-Forwarded-Host` into account.
- Other servers: see their own proxy-header options.

**The `Host` header is chosen by the client.** Do not put `request.url` or `base_url` into a password-reset mail, a `Location` header or any other link people will follow unless the host was checked: an attacker can send any `Host` to a server that does not verify it. Pass `allowed_hosts` to the app:

```python
app = App(allowed_hosts=["api.example.com", "*.example.com"])
```

- A pattern is an exact name (`api.example.com`, `[::1]`), `*.example.com` for any subdomain (not the bare domain), or `*` for every host (an explicit way to switch the check off). Case and the port are ignored; a pattern with a port, a scheme or a path is a `ValueError`.
- A request whose host is not allowed gets `400` (problem+json) when its code reads `host`, `base_url` or `url`. Nothing else is checked: a route that never reads them, and any code that reads `request.headers["host"]` directly, still sees the raw header. The fallback address is judged too.
- Without `allowed_hosts` the host is not checked at all.
- WebSocket guards get `scheme` and `host` values, but the allow-list is not applied to them. Keep an explicit `Origin` allow-list there (see [websocket.md](websocket.md#security)).

## Middleware

```python
Middleware = Callable[[Handler], Handler]
```

A middleware takes the next handler and returns a new handler.

```python
from lettia.core import Context, Handler, Response

def timing(next_: Handler) -> Handler:
    async def run(c: Context) -> Response:
        response = await next_(c)
        response.headers.set("x-served-by", "lettia")
        return response
    return run
```

`next_` never raises for ordinary errors: the app converts exceptions to responses at every layer, around the handler and around each middleware. A middleware therefore always receives a `Response` from `next_`, even when the handler raised `HTTPError` or an unexpected exception, and can decorate or log it. The exception is `ClientDisconnect`, which propagates through every layer.

Configuration goes in a factory that returns the middleware (this is how `cors`, `request_id` and `access_log` work).

### Ordering

- `app.use(*middleware)` adds global middleware; the first one given is the outermost. Calls accumulate: `use(a); use(b, c)` gives `a(b(c(...)))`.
- `app.group(prefix, *middleware)` adds group middleware, applied inside all app middleware and after parent-group middleware.
- `middleware=` on a route adds route middleware, applied inside the group middleware of that route; the first one given is the outermost.
- Full order for a route: app middleware, then group middleware (outer group first), then route middleware, then error conversion, then the handler.
- App middleware also wraps the generated 404 and 405 handlers; group and route middleware do not. App-level middleware order is fixed at build time, so call `use` before the app starts.

### Error conversion

Exceptions raised by a handler or middleware are turned into responses by the app's error handler at the layer where they occur. If an error handler itself raises, the error is logged (`lettia` logger) and a plain `500 Internal Server Error` problem response is returned. Non-`HTTPError` exceptions are logged with traceback by the `lettia` logger (`"unhandled exception on METHOD PATH"`) before the handler runs, unless a [registered exception handler](#exception-handlers-by-type) claims them.

## Built-in middleware

All three are factories returning a `Middleware`. Register with `app.use(...)`.

### request_id

```python
request_id(*, header: str = "x-request-id", generate: Callable[[], str] = ...)
```

Reads `header` from the request. A value matching `[A-Za-z0-9._-]{1,128}` is reused; otherwise `generate()` is called (default: `secrets.token_hex(8)`, 16 hex characters). The id is stored in `RequestId` (a `Key[str]`, `from lettia.middleware import RequestId`; read with `RequestId.get(c)`) and set on the response under `header`.

### access_log

```python
access_log(*, logger: logging.Logger = logging.getLogger("lettia.access"))
```

Emits one `INFO` record per request after the response is produced; nothing is done if the logger is not enabled for `INFO`. Message: `"<METHOD> <path> <status> <duration>ms"` plus ` request_id=<id>` when `request_id` ran outside it. The path is the raw, still percent-encoded path without the query string. Record attributes: `method`, `path`, `status`, `duration_ms`, `client` (IP string or `None`), `request_id` (or `None`). On `ClientDisconnect` it logs status 499 and re-raises; on an exception that escapes to it, it logs 500 and re-raises.

Register `request_id` before (outside) `access_log` so the id is available to the log:

```python
import logging
from lettia.middleware import access_log, cors, request_id

logging.basicConfig(level=logging.INFO)
app.use(request_id(), access_log(), cors(allow_origins=["https://example.com"]))
```

### cors

```python
cors(
    *,
    allow_origins: Sequence[str] = (),
    allow_methods: Sequence[str] = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"),
    allow_headers: Sequence[str] = (),
    allow_credentials: bool = False,
    expose_headers: Sequence[str] = (),
    max_age: int = 600,
)
```

| Option | Meaning |
|---|---|
| `allow_origins` | Exact-match origins. `"*"` allows any origin. Default: none allowed. |
| `allow_methods` | Methods allowed in preflights (compared upper-cased); echoed in `access-control-allow-methods`. |
| `allow_headers` | Allowed request headers (case-insensitive). `["*"]` reflects whatever the preflight requests. |
| `allow_credentials` | Adds `access-control-allow-credentials: true`. Combining with `"*"` in `allow_origins` raises `ValueError`. |
| `expose_headers` | Sent as `access-control-expose-headers` on non-preflight responses. |
| `max_age` | `access-control-max-age` on preflight responses, seconds. |

Behavior:

- Requests without an `Origin` header pass through untouched.
- A preflight is `OPTIONS` with an `Access-Control-Request-Method` header. It is answered directly by the middleware (the route is not called): `204` with `vary`, `access-control-allow-origin`, `access-control-allow-methods`, optional `access-control-allow-headers`, optional credentials, and `access-control-max-age`. If the origin is not allowed, the method is not in `allow_methods`, or a requested header is not allowed, it answers `403` problem+json with detail `"the CORS preflight request was rejected"`.
- Other requests with an `Origin` run normally; the response gets `vary: Origin` (unless `"*"` is configured) and, for allowed origins, `access-control-allow-origin` (the origin, or `*`), credentials and expose headers.
- Register it with `app.use`, not on a group: group middleware does not see preflights for paths with no `OPTIONS` route (they resolve to 405 before any group chain runs).

## Errors

### HTTPError

```python
HTTPError(status, title, detail=None, *, headers=None, extensions=None)
```

`status` must be 400..599 (`ValueError` otherwise). `headers` is `Mapping[str, str]` added to the response. `extensions` is a mapping of extra members merged into the problem body; the keys `type`, `title`, `status` and `detail` are rejected with `ValueError`. `str(exc)` is `"<status> <title>"`.

`Unauthorized(detail=None, *, headers=None)` is `HTTPError(401, "Unauthorized", ...)` and `Forbidden(detail=None, *, headers=None)` is `HTTPError(403, "Forbidden", ...)`.

### Problem response shape

The default handler renders `HTTPError` as `application/problem+json`:

```json
{"type": "about:blank", "title": "Teapot", "status": 418, "detail": "short", "k": 1}
```

`detail` is omitted when `None`; `extensions` members follow. Headers from `HTTPError.headers` are added. Any other exception becomes:

```json
{"type": "about:blank", "title": "Internal Server Error", "status": 500}
```

With `debug=True`, `detail` is `"RuntimeError: secret"` (type name and message). Without it, no detail is exposed.

`problem_response(status, title, detail=None, headers=None, extensions=None) -> Response` builds the same response; use it in custom handlers. Unlike `HTTPError`, it performs no status or extension-key validation.

### Custom error handler

```python
from lettia.core import App, Context, HTTPError, Response, problem_response

async def on_error(c: Context, exc: Exception) -> Response:
    if isinstance(exc, HTTPError):
        return problem_response(exc.status, exc.title, exc.detail, exc.headers, exc.extensions)
    if isinstance(exc, KeyError):
        return problem_response(404, "Not Found", str(exc))
    return problem_response(500, "Internal Server Error")

app = App(error_handler=on_error)
```

The handler sees every `Exception` (including generated 404/405 `HTTPError`s) except `ClientDisconnect`. It replaces the default handler entirely, so handle `HTTPError` yourself if you want its status and headers honored.

### Exception handlers by type

```python
from lettia.core import App, Context, Response

async def on_missing(c: Context, exc: KeyError) -> Response:
    return c.json(404, {"title": "Not Found", "detail": str(exc)})

app = App()
app.add_exception_handler(KeyError, on_missing)
```

`app.add_exception_handler(exc_type, handler)` answers exceptions of `exc_type` and its subclasses with `handler`, an `async (Context, exc) -> Response` whose second parameter can be annotated with `exc_type`. The rest keeps going to the default handler (or to `error_handler=`), so a custom mapping for one exception does not mean rewriting the handling of `HTTPError`.

- **The most specific class wins.** Along the exception's MRO the first registered class is used: a handler for `KeyError` beats one for `LookupError`, which in turn handles every other `LookupError`.
- **A claimed exception is not logged by Core.** There is no `unhandled exception` record and no traceback; log inside the handler when you want one. Exceptions nobody claims are logged as before, whichever handler answers them.
- **`HTTPError` can be registered too.** The handler then answers every `HTTPError`: the generated 404 and 405, the 401 and 403 of guards, 413 and 422. Look at `exc.status` to tell them apart.
- **`Exception` claims everything**, so no unhandled exception is logged any more. Prefer `error_handler=` for a catch-all.
- The response goes through the middleware around the route like any other. A handler that raises is logged (`the error handler failed`) and answered with a plain 500.
- Registering the same class twice, something that is not an `Exception` subclass, or `ClientDisconnect` (it is never turned into a response) is an error (`ValueError` or `TypeError`); registering after the app was built raises `RuntimeError`.
- WebSocket routes keep their own handling (the connection closes with 1011), and the OpenAPI document does not change.

## Lifecycle

Routes and hooks are collected first and assembled once, by `app.build()`. Build happens when the ASGI lifespan starts; if the server sends no lifespan, on the first HTTP or WebSocket request. `build()` is public and idempotent. After a successful build, registration raises `RuntimeError`.

### Startup checks

`build()` collects all problems and raises a single `StartupError` listing them. `err.problems` is a `list[str]`; `str(err)` is `"startup checks failed:\n- problem 1\n- problem 2"`. Problems include:

- invalid path templates (not starting with `/`, `*` not last, mixed text/parameter segments, bad or repeated parameter names);
- route conflicts (`"GET /x conflicts with the registered route GET /x"`);
- invalid group-relative paths;
- problems added by extensions via `add_startup_problem(message)` or `add_startup_check(check)`, where `check` is a `() -> Iterable[str]` run at build time.

```python
from lettia.core import App, StartupError

app = App()
app.get("/x", hello)
app.get("/x", hello)
try:
    app.build()
except StartupError as err:
    print(err.problems)
```

Invalid group prefixes and unsupported methods raise `ValueError` immediately at registration (`group`) or at build (`route` with an invalid method is reported as a startup problem).

### Hooks and lifespan contexts

| Method | Meaning |
|---|---|
| `app.lifespan(context)` | `context` is a zero-argument callable returning an async context manager, e.g. a function decorated with `@contextlib.asynccontextmanager`. |
| `app.on_startup(hook)` | `async () -> None`. |
| `app.on_shutdown(hook)` | `async () -> None`. |

Order at startup: build, lifespan contexts entered in registration order, then startup hooks in registration order. At shutdown: shutdown hooks in reverse registration order, then lifespan contexts left in reverse order.

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def database():
    pool = await open_pool()
    try:
        yield
    finally:
        await pool.close()

app.lifespan(database)
app.on_startup(warm_cache)
app.on_shutdown(flush_metrics)
```

Failure semantics:

- If build, a context or a startup hook raises, the contexts already entered are closed (a failure while closing is only logged), and the app reports `lifespan.startup.failed` with the message: `str(err)` for `StartupError`, otherwise `"ExceptionType: message"`. Shutdown hooks do not run in that case.
- At shutdown every hook runs even if an earlier one failed, and every context is closed even if one fails. Failures are collected and reported as `lifespan.shutdown.failed` with the messages joined by `"; "`.
- If startup is cancelled (`BaseException`), the entered contexts are closed and the exception is re-raised.

## Running as an ASGI app

`App` instances are plain ASGI applications.

```python
# main.py
from lettia.core import App, Context, Response

app = App()

async def hello(c: Context) -> Response:
    return c.text(200, "hello")

app.get("/", hello)
```

```text
uvicorn main:app
```

Uvicorn sends the lifespan scope, so startup checks run before the server accepts connections, and a `StartupError` makes the server exit. `app` can also be wrapped by other ASGI middleware, since it follows the standard `(scope, receive, send)` interface.

### Wrapping with ASGI middleware

Compression, host checks and HTTPS redirects belong in front of the app, and any ASGI middleware can provide them. This example uses Starlette's, which Lettia does not depend on; install it yourself:

```python
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

application = GZipMiddleware(app, minimum_size=1000)
application = TrustedHostMiddleware(application, allowed_hosts=["api.example.com"])
application = HTTPSRedirectMiddleware(application)  # outermost: runs first
```

```text
uvicorn main:application
```

- Serve `application`, not `app`. The lifespan scope passes through, so the startup checks still run.
- Starlette's `GZipMiddleware` skips `text/event-stream` by default, which an SSE stream needs: compressing it would hold the events back. Keep that exclusion with any other compressor.
- Behind a reverse proxy the proxy usually terminates TLS and checks the host; do the redirect or the check there instead, and tell the server which proxies to trust (for uvicorn `--proxy-headers` and `--forwarded-allow-ips`).
- `TestClient` takes the `App` itself, so tests exercise `app`, not the wrapper.

## See also

- [getting-started.md](getting-started.md)
- [contract.md](contract.md)
- [guards.md](guards.md)
- [websocket.md](websocket.md)
- [testing.md](testing.md)
- [extensions.md](extensions.md)
