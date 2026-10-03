---
title: Context API
---

# `Context`

`Context` is the request-local object passed to HTTP handlers and middleware.
It exposes the ASGI scope without hiding the request lifecycle behind a large
dependency-injection abstraction.

## Data model

```python
@define(slots=True)
class Context:
    scope: HTTPScope
    receive: HTTPReceive
    send: HTTPSend
    path_params: dict[str, str] = field(factory=dict)
    state: StateStore = field(factory=StateStore)
```

`path_params` is populated by the router after a match. `state` is a mutable,
request-local `StateStore` shared by middleware and the handler. It accepts
only typed `StateKey[T]` keys; it is not a string dictionary or application
service registry.

## Request properties

| API | Behavior |
|---|---|
| `ctx.path` | Read/write path from `scope["path"]`; writable before routing |
| `ctx.method` | Uppercase HTTP method from `scope["method"]` |
| `ctx.path_params` | Router-provided string parameters |
| `ctx.query_params` | Lazy `dict[str, list[str]]` from `query_string` |
| `ctx.headers` | Lazy lower-case header mapping |
| `ctx.cookies` | Lazy mapping parsed from the Cookie header |
| `ctx.state` | Request-local typed-key state |

`ctx.path` preserves the scope path, including any mount prefix. Dispatch
separately resolves `root_path` at a complete segment boundary for routing and
405 detection. Rewriting `ctx.path` before dispatch still works.

Convenience accessors return the first value or a default:

```python
ctx.query_param("page", default="1")
ctx.header("authorization")
ctx.cookie("session")
```

Query parameters, headers, and cookies are parsed on first access and cached
for the lifetime of the context.

## Body API

```python
await ctx.body(max_bytes: int | None = None) -> bytes
await ctx.json() -> JSONValue
await ctx.text() -> str
```

`body()` consumes ASGI `http.request` messages once and caches the complete
body. Later calls reuse the cached bytes. `max_bytes` is enforced while
reading, not only after the complete body has arrived.

The strictest supplied limit persists for the request, including later calls
without `max_bytes`. Content-Length is checked before reading. A body rejected
with 400 or 413 remains rejected on subsequent body/text/JSON reads and releases
cached chunks. Cancellation preserves partial chunks for a later body read.

Concurrent `body()` calls share one reader. During streamed responses, Writer
coordinates a Context-owned disconnect callback that also uses this cache, so
lazy body reads from the response generator retain all request data.
The public `ctx.receive` remains the raw ASGI entry point: direct reads bypass
the framework cache, limits and rejection tracking. Do not read it concurrently
with managed body APIs or a streamed response; use the body APIs instead.
If the body was rejected, the monitor discards remaining ASGI body events
without buffering them and does not make the rejected body readable again.

| Situation | Result |
|---|---|
| Body exceeds `max_bytes` | `HTTPException(413, ...)` |
| Client disconnects while reading | `HTTPException(400, ...)` |
| Unexpected ASGI message | `HTTPException(400, ...)` |
| Invalid UTF-8 or JSON | `HTTPException(400, ...)` |
| JSON integer exceeds Python's decoding length limit | `HTTPException(400, ...)` |
| JSON exceeds the decoder or validation recursion limit | `HTTPException(400, ...)` |
| Empty body passed to `json()` | `None` |

Use `body_limit()` when every route should share the same limit.

## Typed binding

```python
await ctx.bind(target_type: type[T]) -> T
```

For `POST`, `PUT`, and `PATCH`, binding combines JSON object fields with query
parameters. JSON values take precedence when the same key exists in both
sources.

The built-in selection order is:

1. `attrs` class → `AttrsBinder`;
2. standard-library dataclass → `DataclassBinder`; and
3. Pydantic `BaseModel` when Pydantic is installed → `PydanticBinder`.

Invalid attrs/dataclass coercion or construction is converted to HTTP 400.
Pydantic validation errors also become 400, while programming errors raised by
its validators propagate unchanged, including `TypeError` and `ImportError`.
Only failure to import the optional Pydantic package triggers the unsupported
target fallback. Custom binding can be performed directly through a `Binder`
implementation.
Attrs uses constructor aliases, and attrs/dataclass `init=False` fields are
ignored. Dataclass constructor `InitVar[T]` parameters use the same scalar and
nullable conversion rules as `T`, including inherited and keyword-only
parameters. Omitted parameters retain their defaults. Numeric conversion
overflow is a 400 input error; unsupported
constructor annotations remain configuration `TypeError` values. See the
[binding contracts](extensions.md#binder-protocol) for details.

```python
@define(slots=True)
class Search:
    q: str
    page: int = 1


@app.get("/search")
async def search(ctx: Context) -> dict[str, JSONValue]:
    params = await ctx.bind(Search)
    return {"q": params.q, "page": params.page}
```

## Background tasks

```python
ctx.add_background_task(
    func: Callable[..., object],
    *args: object,
    **kwargs: object,
) -> None
```

Context stores the tasks; App executes them in registration order after Writer
reports completion eligibility and finishes iterator cleanup. Successful error
and fallback responses follow the same completion path. Observed disconnects,
cancellation, transport failures, propagated source errors and cleanup failures
suppress the tasks. Individual task exceptions are logged; later queued tasks
can still run.

Both sync and async callables are accepted and run in the current application
call, not a separate worker. They are suitable for short best-effort work; durable
or long-running work belongs in an external queue. See
[deployment reliability](../deployment.md#background-tasks-and-reliability).

## Aborting a request

```python
ctx.abort(
    status_code: int,
    detail: object = None,
    headers: dict[str, str] | None = None,
) -> NoReturn
```

This raises `HTTPException`. The application error handler converts it into a
response while preserving its status code, detail, and headers.

## State boundaries

Use `ctx.state` for request-scoped values. State uses typed key instances, not
string dictionary keys:

```python
from lettia import REQUEST_ID

request_id = ctx.state.get(REQUEST_ID)
```

`get()` returns `T | None`, `require()` returns `T` or raises `KeyError`, and
`discard()` removes one key. `require(REQUEST_ID)` therefore returns `str` and
raises `KeyError` when request-ID middleware did not run. A WebSocket
connection has a separate `WebSocketContext.state` `StateStore`; neither store
is shared with `App` or another request.
