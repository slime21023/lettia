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
    scope: dict[str, Any]
    receive: Any
    send: Any
    path_params: dict[str, str] = field(factory=dict)
    state: dict[str, Any] = field(factory=dict)
```

`path_params` is populated by the router after a match. `state` is a mutable
request-local dictionary shared by middleware and the handler.

## Request properties

| API | Behavior |
|---|---|
| `ctx.path` | Read/write path from `scope["path"]`; writable before routing |
| `ctx.method` | Uppercase HTTP method from `scope["method"]` |
| `ctx.path_params` | Router-provided string parameters |
| `ctx.query_params` | Lazy `dict[str, list[str]]` from `query_string` |
| `ctx.headers` | Lazy lower-case header mapping |
| `ctx.cookies` | Lazy mapping parsed from the Cookie header |
| `ctx.state` | Request-local mutable state |

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
await ctx.json() -> Any
await ctx.text() -> str
```

`body()` consumes ASGI `http.request` messages once and caches the complete
body. Later calls reuse the cached bytes. `max_bytes` is enforced while
reading, not only after the complete body has arrived.

| Situation | Result |
|---|---|
| Body exceeds `max_bytes` | `HTTPException(413, ...)` |
| Client disconnects while reading | `HTTPException(400, ...)` |
| Unexpected ASGI message | `HTTPException(400, ...)` |
| Invalid UTF-8 or JSON | `HTTPException(400, ...)` |
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

Invalid coercion or model construction is converted to HTTP 400. Custom
binding can be performed directly through a `Binder` implementation.

```python
@define(slots=True)
class Search:
    q: str
    page: int = 1


@app.get("/search")
async def search(ctx: Context) -> dict[str, Any]:
    params = await ctx.bind(Search)
    return {"q": params.q, "page": params.page}
```

## Background tasks

```python
ctx.add_background_task(
    func: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> None
```

Tasks are queued on the context and executed after response messages are sent.
Both sync and async callables are accepted. They are suitable for short
best-effort work; durable or long-running work belongs in an external queue.

## Aborting a request

```python
ctx.abort(
    status_code: int,
    detail: Any = None,
    headers: dict[str, str] | None = None,
) -> NoReturn
```

This raises `HTTPException`. The application error handler converts it into a
response while preserving its status code, detail, and headers.

## State boundaries

Use `ctx.state` for request-scoped values:

```python
ctx.state["request_id"] = "request-123"
```

Use `app.state` for application-wide resources. A WebSocket connection has a
separate `WebSocketContext.state` dictionary.
