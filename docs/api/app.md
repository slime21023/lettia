---
title: App API
---

# `App`

`App` is Lettia's ASGI entrypoint and application composition root. It owns the
router, middleware registrations, application state, error handler, and
lifespan hooks.

## Construction

```python
from lettia import App

app = App()
```

An application starts with an empty `Router` and an application-wide mutable
`state` dictionary. Request-local values belong in `Context.state`, not in
`App.state`.

## Registration API

### Routes

```python
app.add_route(
    method: str,
    path: str,
    handler: Callable[..., Any],
    name: str | None = None,
    middlewares: list[Middleware] | None = None,
) -> None
```

The decorator helpers `get`, `post`, `put`, `delete`, and `patch` accept
`(path, name=None)` and return a decorator. `websocket(path, name=None)`
registers a WebSocket route using the special `WEBSOCKET` method internally.

```python
@app.post("/users", name="create_user")
async def create_user(ctx: Context) -> dict[str, str]:
    return {"status": "created"}
```

Route registration invalidates the compiled handler chains. The chains are
rebuilt at the next HTTP request or during lifespan startup.

### Global and pre-routing middleware

```python
app.use(*middlewares: Middleware) -> App
app.use_pre(*pre_middlewares: Middleware) -> App
```

`use()` wraps dispatch, including routing failures. `use_pre()` wraps the
global chain and runs before route matching. Both methods return the same app
for fluent configuration.

```python
app.use(recover(), request_logger())
app.use_pre(normalize_path)
```

### Groups

```python
app.group(prefix: str, *middlewares: Middleware) -> Group
```

The returned `Group` composes a path prefix and inherited route middleware.
Nested groups concatenate both prefixes and middleware lists.

### Reverse URL lookup

```python
app.url_for(name: str, **kwargs: Any) -> str
```

The named route must exist. Missing parameters raise `KeyError`, unexpected
parameters raise `TypeError`, and parameter values are URL-encoded according
to the route type.

## Error handling

```python
app.set_error_handler(
    handler: Callable[[Context, Exception], Awaitable[Any]]
) -> Callable[[Context, Exception], Awaitable[Any]]

@app.error_handler
async def handle_error(ctx: Context, exc: Exception) -> Any:
    ...
```

The handler receives both the request context and the exception. It may return
any value accepted by `normalize_response()`. `HTTPException` represents an
expected HTTP failure; unexpected exceptions are converted to a 500 response
by the default handler.

## Lifespan

```python
app.on_event(
    event_type: str,
) -> Callable[[Callable[[], Any]], Callable[[], Any]]
```

Supported event types are `startup` and `shutdown`. Handlers may be sync or
async. Startup compiles route and middleware chains before reporting
`lifespan.startup.complete`.

```python
@app.on_event("startup")
async def startup() -> None:
    app.state["pool"] = await create_pool()


@app.on_event("shutdown")
async def shutdown() -> None:
    await app.state["pool"].close()
```

## ASGI dispatch

`App.__call__(scope, receive, send)` branches on the ASGI scope type:

| Scope | Handler |
|---|---|
| `http` | Context, middleware, router, response writer, background tasks |
| `websocket` | WebSocket route and `WebSocketContext` |
| `lifespan` | Startup and shutdown hooks |

HTTP handlers may be synchronous or asynchronous. Their return values are
normalized after middleware completes. WebSocket routes intentionally bypass
the HTTP middleware and response pipeline.

## Composition contract

The effective HTTP chain is:

```text
pre-routing middleware
  global middleware
    router dispatch
      route/group middleware
        handler
```

The chain is compiled by `build_chain()` so middleware wrapper construction is
not repeated for every request.
