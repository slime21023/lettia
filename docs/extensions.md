# Extensions

Seven optional extensions ship under `lettia.extensions`: `di` (request-scoped dependency injection on top of rodi), `tasks` (in-process background tasks), `staticfiles` (static file serving), `docs` (interactive OpenAPI viewers), `sse` (Server-Sent Events), `sessions` (signed-cookie sessions) and `ratelimit` (in-memory rate limiting). They plug into an `App` through `app.use(...)`, `app.on_shutdown(...)` and ordinary route registration; nothing in core imports them.

| Extension | Import | Extra |
|---|---|---|
| DI | `lettia.extensions.di` | `lettia[di]` (installs `rodi>=2.0.0`) |
| Background tasks | `lettia.extensions.tasks` | none |
| Static files | `lettia.extensions.staticfiles` | none |
| Docs viewers | `lettia.extensions.docs` | none |
| Server-Sent Events | `lettia.extensions.sse` | none |
| Sessions | `lettia.extensions.sessions` | none |
| Rate limiting | `lettia.extensions.ratelimit` | none |

`app.use(*middleware)` takes global middleware; the first one given is the outermost. Both middleware classes below are plain callables `middleware(next_handler) -> handler`.

## DI (`lettia.extensions.di`)

```
pip install "lettia[di]"
```

### Public API

| Name | Kind | Meaning |
|---|---|---|
| `Container` | class | `rodi.Container` subclass with `wire()` |
| `DIMiddleware(container)` | class | creates one rodi scope per request |
| `Injected[T]` | descriptor | resolves `T` from the current request scope on attribute access |
| `SCOPE_KEY` | `Key[rodi.ActivationScope]` | the request scope on `Context.state` |
| `current_scope` | `ContextVar[rodi.ActivationScope \| None]` | the active scope, default `None` |
| `get_current_scope()` | function | `current_scope.get()` |
| `teardown_scope(scope)` | async function | calls `aclose()`/`close()` on scoped instances |

### Wiring recipe

```python
from typing import Protocol

from lettia import App, Context, Ok, op, public
from lettia.extensions.di import Container, DIMiddleware, Injected
from lettia.testing import TestClient


class Config:
    env = "prod"


class Session:
    def __init__(self) -> None:
        self.closed = False
        self.rows = ["alice", "bob"]

    async def aclose(self) -> None:
        self.closed = True


class UserApi(Protocol):
    @op.get("/users", guard=public)
    async def list_users(self, c: Context[None], req: None) -> Ok[list[str]]: ...


class UserService(UserApi):
    db: Injected[Session]          # per-request, resolved on access

    def __init__(self, config: Config) -> None:   # resolved once at wire()
        self.config = config

    async def list_users(self, c: Context[None], req: None) -> Ok[list[str]]:
        return Ok(self.db.rows)


container = Container()
container.add_singleton(Config, Config())
container.add_scoped(Session)

app = App()
app.use(DIMiddleware(container))
container.wire(app, [UserService])
```

Order of calls: register services on the container, `app.use(DIMiddleware(container))`, then `container.wire(app, [...])`. `DIMiddleware(container)` calls `container.build_provider()` immediately, so register every service the middleware's provider needs before constructing it. `DIMiddleware` may sit anywhere among global middleware; it only has to wrap the handler. It also accepts an already-built `rodi.Services` instead of a container; `middleware.provider` returns the underlying `rodi.Services`.

### `Container`

`Container(*args, **kwargs)` forwards everything to `rodi.Container`; the rodi registration API (`add_scoped`, `add_transient`, `add_scoped_by_factory`, ...) is inherited unchanged.

* `build_provider()` builds once and caches the `rodi.Services`. Later calls return the same object. Register everything before the first call (`DIMiddleware(container)` and `wire()` both trigger it).
* `add_singleton(base_type, concrete_type=None)` returns `self`. If `concrete_type` is given and is not a class, it is registered as a pre-built instance (`add_instance`); otherwise it is rodi's normal singleton registration (a class, or `None` to construct `base_type` itself).
* `wire(app, services, *, middleware=None)` does, per class in `services`: bind `Injected` descriptors (below); register the class with rodi unless it is already registered. It then builds the provider and, for each class, obtains the instance from the provider and mounts it on `app` as a contract implementation (see [contract.md](contract.md)). `middleware` is the mapping of [`mount(middleware=...)`](contract.md#middleware-for-single-operations), keyed by contract method; each entry goes to the service that implements that method, and an entry no wired service implements is a startup problem. Each service instance is created once and shared by all requests, so its constructor may only depend on singletons and transients. If resolving a service reaches a scoped dependency (directly or through a transient), `wire()` does not mount it and records a startup problem, reported by `StartupError` when the app is built: `UserService depends on scoped Session through its constructor, so one instance would be shared by every request; declare it as Injected[T] instead`. Resolution stops at the first scoped dependency, before its factory runs, so the check creates nothing; a service that is itself registered as scoped is fine. Use `Injected` for anything that must be per request. A missing constructor dependency raises a rodi exception (e.g. `rodi.CannotResolveParameterException`, a `rodi.DIException`) from `wire()`, i.e. at startup.

### `Injected[T]`

`Injected(service_type=None)` is a descriptor. Accessed on the class it returns itself (`Service.db.service_type`); accessed on an instance it resolves the service.

Three ways to declare one:

```python
class A:
    db: Injected[Session]            # annotation only; bound by Container.wire()

class B:
    db: Injected[Session] = Injected()   # type taken from the annotation at class creation

class C:
    db = Injected(Session)           # explicit type; works with no annotation and no wire()
```

The annotation-only form (`A`) is turned into a descriptor by `Container.wire()`. It scans the class and every base class in its MRO and, for each annotation of the form `Injected[X]` or `ClassVar[Injected[X]]` that is not already a class attribute, sets an `Injected` descriptor on the class that declares it, so fields inherited from a base class are bound too. Annotations written as strings (including `from __future__ import annotations`) are resolved in the module of the declaring class. A string annotation that is `Injected[...]` (under any alias) but whose type cannot be resolved raises `TypeError` from `wire()`; an unresolvable annotation that has nothing to do with `Injected` is left alone. A bare `Injected` or `ClassVar[Injected]` annotation, with no `[T]`, raises `TypeError` from `wire()` because the service type is unknown. `Injected()` assigned without a type, and without an `Injected[T]` annotation (`db = Injected()` or `db: Injected = Injected()`), leaves the type unset: access raises `TypeError("Injected descriptor '<name>' has no resolved service type")`.

Resolution on access:

1. No active scope (`current_scope` is `None`): `RuntimeError("Cannot resolve Injected descriptor '<name>' outside of an active request scope")`. This happens when `DIMiddleware` is not installed, or when the access occurs outside a request (for example in a background task or at import time).
2. If the service type has a callable `__injected__` attribute, it is called with no arguments (typically a classmethod) and the result is cached in the scope for the rest of the request. This is how `Injected[BackgroundTasks]` works. The type does not need to be registered in the container.
3. Otherwise `scope.get(service_type)`. Lifetimes follow rodi: scoped services are created once per request, transient services are created on every access, singletons are shared. An unregistered type raises `rodi.CannotResolveTypeException` (a 500 at request time).

You can also take the scope directly: `SCOPE_KEY.get(c)` on the request `Context`, or `get_current_scope()`.

### Dataclass services

Constructor injection works with `@dataclass(frozen=True, slots=True)` services: rodi reads the generated `__init__`.

```python
from dataclasses import dataclass
from typing import ClassVar

@dataclass(frozen=True, slots=True)
class UserService(UserApi):
    config: Config                 # constructor injection
    db: ClassVar[Injected[Session]]  # not a dataclass field; bound by wire()

    async def list_users(self, c, req):
        return Ok(self.db.rows)
```

A plain `db: Injected[Session]` annotation on a dataclass would become an `__init__` field and `wire()` could not resolve it. Declare it as `ClassVar[Injected[Session]]` instead: dataclasses ignore `ClassVar` fields, `wire()` binds it, and pyright, mypy and pyrefly all type `self.db` as `Session`. The unannotated `db = Injected(Session)` form also works.

### Request scope lifecycle

For every request `DIMiddleware`:

1. creates a scope (`provider.create_scope()`), sets `current_scope` and `SCOPE_KEY`;
2. runs the next handler;
3. in a `finally`: runs `teardown_scope(scope)`, resets `current_scope`, and calls `scope.dispose()`. This happens on success and on unhandled exceptions (the response is then a 500).

`teardown_scope` walks the scope's scoped instances in reverse creation order (LIFO). For each instance: if it has `aclose`, call it (awaited if it returns an awaitable); otherwise if it has `close`, call it. Only one of the two is called. An exception from either is logged to the `lettia.extensions.di` logger and swallowed, so one failing teardown does not stop the others or change the response. Only instances stored in the scope's scoped services are torn down: scoped registrations and `__injected__` results, not singletons or transients.

## Background tasks (`lettia.extensions.tasks`)

No extra dependencies.

### Public API

| Name | Meaning |
|---|---|
| `BackgroundTasksMiddleware(app=None, *, shutdown_timeout_sec=10.0)` | per-request collector and runner |
| `BackgroundTasksMiddleware.attach(app)` | registers `drain` with `app.on_shutdown` |
| `await BackgroundTasksMiddleware.drain(timeout_sec=None)` | waits for running task batches |
| `BackgroundTasks` | per-request collection |
| `BackgroundTasks.add_task(fn, *args, **kwargs)` | queue a call; returns `self` (chainable) |
| `BackgroundTasks.of(c)` | classmethod; the collection for `Context` `c` |
| `len(tasks)`, `bool(tasks)` | number of queued tasks / whether any are queued |
| `TASKS_KEY` | `Key[BackgroundTasks]` on `Context.state` |

### Wiring recipe

```python
from lettia import App, Context
from lettia.core import Response
from lettia.extensions.tasks import BackgroundTasks, BackgroundTasksMiddleware

app = App()
app.use(BackgroundTasksMiddleware(app))     # attaches the shutdown drain


def send_email(to: str, subject: str = "hi") -> None:   # sync: runs in a thread
    ...


async def audit(event: str) -> None:                      # async: awaited on the loop
    ...


async def signup(c: Context) -> Response:
    BackgroundTasks.of(c).add_task(send_email, "a@b.c", subject="welcome").add_task(audit, "signup")
    return Response(202)


app.post("/signup", signup)
```

`BackgroundTasksMiddleware(app)` is shorthand for `BackgroundTasksMiddleware()` followed by `attach(app)`. If you construct it without `app`, call `middleware.attach(app)` yourself or the shutdown drain is not installed (you can still call `await middleware.drain()` manually). The middleware may be placed anywhere among global middleware.

### Semantics

* Each request gets a fresh `BackgroundTasks`. `BackgroundTasks.of(c)` returns it, or raises `RuntimeError` mentioning `BackgroundTasksMiddleware` if the middleware is not mounted.
* Alternatively, with `lettia.extensions.di`, declare `tasks: Injected[BackgroundTasks]` on a service. `BackgroundTasks.__injected__` returns the current request's collection from a context variable, so no container registration is needed. Outside a request it raises the same `RuntimeError`. Inside a running task `BackgroundTasks.__injected__()` returns the task's own batch, so a task can queue more tasks on it; they run in the same FIFO batch. The DI scope and the tasks middleware are independent; either may be outer.
* When the inner handler returns and at least one task is queued, the middleware attaches a start callback to the response's `after_send` (see [core.md](core.md#after-the-response-is-sent)). The batch starts only once the whole response was sent and a streamed body was closed: it then becomes a single `asyncio` task that runs the queued calls in FIFO order, one at a time, and the request does not wait for it. Nothing starts while the response is still being sent.
* The batch does not start when the handler, the send or the stream fails, when the request is cancelled, or when an outer middleware replaces the response with another one (the callback belongs to the response that was received). Which status the response has does not matter: a 4xx or 5xx response that is sent, including an error response produced by the app's error handling after the handler raised, still starts the tasks queued before the error. "Sent" means the ASGI `send` calls returned; the client may still not have received the response. A server that silently discards sends after the client disconnected (uvicorn does) does not raise, so the batch still starts when a client drops the connection mid-response.
* Async callables (`inspect.iscoroutinefunction`) are awaited on the event loop. Every other callable is run with `asyncio.to_thread`, so a blocking function does not block the loop. If a sync callable returns an awaitable (a lambda or `functools.partial` around an async function, or an object with `async def __call__`), the awaitable is then awaited on the event loop, so the task still runs.
* A task that raises `Exception` is logged with `logger.exception` on the `lettia.extensions.tasks` logger and the remaining tasks still run. Nothing is reported to the client.
* Tasks hold no request state of their own. The DI scope is disposed when the handler chain returns, before the response is sent and so before any task runs, and a task cannot resolve `Injected` attributes (it raises `RuntimeError`). Pass immutable data or identifiers, not objects tied to the request: a request-scoped session or connection that was resolved before `add_task` is already closed when the task runs, and so is any stream or file handle the request owns. Let the task open and close what it needs.
* The batch is only started if at least one task was queued when the handler returned. Tasks added later, for example from inside a streaming body, are not run on their own; if the batch was started anyway, they join it and run after the earlier ones.
* If another middleware has set `after_send` on the response, its callback runs first, then the batch starts. An exception from that callback is logged and does not lose the batch.

```python
from pathlib import Path


def append_audit(path: Path, event: str) -> None:       # sync: runs in a thread
    with path.open("a", encoding="utf-8") as log:        # opened and closed by the task
        log.write(event + "\n")


AUDIT_LOG = Path("audit.log")


async def signup(c: Context) -> Response:
    BackgroundTasks.of(c).add_task(append_audit, AUDIT_LOG, "signup")   # path and text, not an open file
    return Response(202)
```

A pool or client shared by several tasks must outlive them. `app.lifespan(...)` contexts are left after the shutdown hooks, so a resource held there is still open while `drain` waits, unless `drain` times out.

### Shutdown drain

`drain(timeout_sec=None)` waits (`asyncio.wait`) for the batches currently running, up to `timeout_sec`, defaulting to the `shutdown_timeout_sec` given to the constructor (10 s). It returns immediately if nothing is running. When the timeout elapses it simply stops waiting: unfinished tasks are not cancelled and no error is raised. With `app` given (or `attach`), `drain` is registered as a shutdown hook, so `async with TestClient(app)` or an ASGI lifespan shutdown waits for in-flight tasks. `drain` only waits for batches already started; a response that is still being sent has not started its batch yet, and tasks queued after `drain` returns are not covered. Shutdown hooks run last registered first, so a hook registered after `BackgroundTasksMiddleware(app)` runs before the drain: do not close a shared resource there that tasks still use.

### Limitations

In-process only: there is no persistence, retry, scheduling, or cross-process queue. Queued tasks are lost if the process crashes or is killed before or while they run, and tasks still running after the drain timeout are lost when the process exits. A response that is sent does not guarantee that the tasks run, and tasks never run for a response that fails to send. Use a real job queue, or persist the work in the same transaction as the request (an outbox), for work that must not be lost.

## Static files (`lettia.extensions.staticfiles`)

No extra dependencies. Exports `StaticFiles`, `ByteRange`, `parse_range_header`.

### Mounting

```python
from lettia import App
from lettia.extensions.staticfiles import StaticFiles

app = App()
StaticFiles.mount(app, "/static", "./public")
```

`StaticFiles.mount(app, prefix, directory, **options)` constructs a `StaticFiles` handler, registers it as `GET /<prefix>/*` and as `GET /<prefix>`, and returns the handler. `prefix` is normalized to `"/" + prefix.strip("/")`. With `prefix="/"` the handler is registered as `GET /*` and `GET /`, so a root mount works:

```python
StaticFiles.mount(app, "/", "./public", html=True)
```

Routes registered on a more specific path (for example `app.get("/api/hello", ...)`) take precedence over the `/*` wildcard, so API routes can coexist with a root mount. The wildcard does make `GET` match every path: an unknown path gets the static handler's 404, and a non-`GET` request to an unknown path gets `405` with `Allow: GET, HEAD` instead of the router's `404`.

`StaticFiles` is itself a handler: `async (c: Context) -> Response`. It reads the path remainder from `c.path_params["*"]`, so it must be registered on a route ending in `/*`.

### Options

Same keyword-only options for `StaticFiles(directory, ...)` and `StaticFiles.mount(app, prefix, directory, ...)`.

| Option | Type | Default | Meaning |
|---|---|---|---|
| `directory` | `str \| Path` | required | Root directory; resolved at construction. `ValueError` if it does not exist or is not a directory |
| `html` | `bool` | `False` | SPA mode: fall back to the root index file for unknown non-asset paths and for directories without an index |
| `cache_control` | `str \| None` | `"public, max-age=3600"` | Value of `Cache-Control`; `None` (or empty) omits the header |
| `chunk_size_bytes` | `int` | `65536` | Files (or ranges) smaller than this are read into memory and sent in one body; larger ones are streamed in chunks of this size |
| `follow_symlinks` | `bool` | `False` | Allow symlinks inside the tree |
| `allow_hidden` | `bool` | `False` | Allow path segments beginning with `.` |
| `index_name` | `str` | `"index.html"` | Index file name for directories and for SPA fallback |

### Path resolution and failures

Every failure below is `HTTPError(404, "Not Found")`, rendered by the app as a problem+json 404; the response never distinguishes the reason.

| Condition | Result |
|---|---|
| subpath contains `:` or a NUL byte (blocks Windows alternate data streams) | 404 |
| a segment starts with `.` (e.g. `/.env`, `/.git/config`) and `allow_hidden=False` | 404 |
| resolved path is outside `directory` (`..`, encoded `%2e%2e`) | 404 |
| path, or any parent up to `directory`, is a symlink, or `directory` itself is a symlink, and `follow_symlinks=False` | 404 |
| path is a directory with an index file | serves `<dir>/<index_name>` |
| path is a directory without an index, `html=False` | 404 |
| path is a directory without an index, `html=True` | root index, else 404 |
| file missing, `html=False` | 404 |
| file missing, `html=True`, extension not in the asset list | root index (200), else 404 |
| file missing, `html=True`, extension in the asset list | 404 |
| method other than GET/HEAD | 405 with `Allow: GET, HEAD` (router behavior) |

Asset extensions that never fall back (compared case-insensitively on the missing path's suffix): `.avif .css .eot .gif .ico .jpeg .jpg .js .json .map .mjs .mp3 .mp4 .ogg .pdf .png .svg .ttf .wasm .wav .webm .webp .woff .woff2 .xml`. Any other suffix, including none or `.html`, falls back, so `/missing.png` is 404 but `/dashboard/x` and `/x.html` return the index.

A trailing slash is ignored: `/static`, `/static/` and `/static/sub` (a directory with its own `index.html`) all work. In SPA mode an existing directory with no index (`/empty`) also returns the root index.

### Response headers

On 200, 206, 304 and 416:

| Header | Value |
|---|---|
| `Accept-Ranges` | `bytes` |
| `ETag` | weak, `W/"<mtime_ns hex>-<size hex>"` |
| `Last-Modified` | HTTP date of the file mtime |
| `Cache-Control` | the `cache_control` option, if set |
| `Content-Type` | from `mimetypes.guess_type` on the file name; `application/octet-stream` if unknown; `; charset=utf-8` appended for `text/*`, `application/javascript`, `application/json`, `application/xml` |
| `Content-Length` | on 200 and 206 (size of the body sent); 416 sends an empty body |

When `html=True` falls back to the index, the index file's own type, ETag and mtime are used.

### Conditional requests

Checked before `Range`:

* `If-None-Match`: a list of entity tags (`"a", W/"b"`) or `*`. The weak comparison applies, so the `W/` prefix is ignored on both sides. A matching tag (or `*`) returns 304 with the headers above and no body. Repeated `If-None-Match` header lines are joined into one list.
* `If-Modified-Since`: parsed as an HTTP date; if its whole-second timestamp is at or after the file mtime, 304. An unparsable value is ignored. It is only evaluated when the request has no `If-None-Match` header; with `If-None-Match` present, a non-matching tag falls through to `Range` or a full 200 response.

### Range requests

Only a single `bytes=` range is honored. Evaluated by `parse_range_header(header_value, total_size)`, which returns a `ByteRange(start, end, total_size)` (frozen dataclass with `.length` and `.content_range`), `"unsatisfiable"`, or `"ignore"`.

| Header (size 256000) | Result |
|---|---|
| `bytes=0-9` | 206, `Content-Range: bytes 0-9/256000`, 10 bytes |
| `bytes=500-` | 206, from byte 500 to the end |
| `bytes=-5` | 206, the last 5 bytes |
| `bytes=0-999999` | end clamped to size-1 |
| `bytes=999999-` (start >= size) | 416, `Content-Range: bytes */256000`, empty body |
| `bytes=5-2` (end < start) | 416 |
| `bytes=-0` | 416 |
| any range on an empty file | 416, `Content-Range: bytes */0` |
| `bytes=0-1,3-4` (multiple ranges) | ignored, full 200 |
| other unit, missing `-`, non-numeric, `bytes=-` | ignored, full 200 |

### HEAD and bodies

HEAD is served by the GET route: the same status and headers, including `Content-Length`, with the body dropped by the ASGI layer. Large files are streamed (`asyncio.to_thread` reads of `chunk_size_bytes`) and the file is closed when the stream finishes or is aborted.

## Docs viewers (`lettia.extensions.docs`)

`mount_docs(app, prefix="/docs", provider="scalar", *, title=None, favicon=None, theme="auto", assets=None)` serves the app's OpenAPI document in Scalar, Swagger UI or Redoc. `App(docs_url=..., docs_ui=...)` calls the same function. It registers only the viewer page: the document stays at `app.openapi_url`. With `assets=<directory>` the viewer loads the pinned bundles from `{prefix}/assets/*` instead of a CDN, checked against their pinned hashes at startup. See [openapi.md](openapi.md#interactive-docs).

## Server-Sent Events (`lettia.extensions.sse`)

`ServerSentEvent(data, event=None, id=None, retry=None)` is one event; `data` is encoded as JSON, so a browser reads it with `JSON.parse(event.data)`. `EventStream[T]` is a response class for a contract operation: declare it as the return type and return `EventStream[Tick](events())`, where `events()` is an async iterator of `ServerSentEvent[Tick]`. It sends `Cache-Control: no-cache` and `X-Accel-Buffering: no`, a `: connected` comment first and `: keep-alive` comments while idle (`heartbeat_sec`, 15 s by default, `None` turns it off), and stops and closes the source when the client disconnects. For routes outside a contract, `event_stream(events, heartbeat_sec=15.0)` returns a `Response`. The OpenAPI document describes the media type `text/event-stream` and lists the event type under `x-lettia-item-schema`. A streamed source must not use request-scoped resources (the request scope is closed when the handler returns), and background tasks queued by the request start only after a finite stream ended normally. See [contract.md](contract.md#streamed-responses) and `examples/sse_api.py`.

## Sessions (`lettia.extensions.sessions`)

`app.use(SessionMiddleware([secret_key]))`, then `Session.of(c)` in a handler. The cookie is signed with HMAC-SHA256, not encrypted, and there is no server-side storage. See [sessions.md](sessions.md).

## Rate limiting (`lettia.extensions.ratelimit`)

`app.use(rate_limit("5/minute"))`, or `app.group("/login", rate_limit("5/minute"))`. Counts live in one process. See [ratelimit.md](ratelimit.md).

## See also

* [core.md](core.md): `App`, middleware, hooks, handlers
* [contract.md](contract.md): contract implementations that `Container.wire` mounts
* [testing.md](testing.md): `TestClient`
* [getting-started.md](getting-started.md)
