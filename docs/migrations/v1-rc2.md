---
title: v1-rc2 Migration Notes
---

# v1-rc2 Migration Notes

v1-rc2 is a pre-1.0 candidate and includes intentional breaking cleanup of
internal APIs.

## App internals

The following mutable `App` attributes are now private:

- `router`
- `global_middlewares`
- `pre_middlewares`
- `on_startup`
- `on_shutdown`

Use the public registration methods instead:

```python
app.use(middleware)
app.use_pre(middleware)

@app.on_event("startup")
async def startup() -> None:
    ...

@app.on_event("shutdown")
async def shutdown() -> None:
    ...
```

Routes should be registered through `get`, `post`, `add_route`, `group`, or
`websocket`. Use `url_for` for URL generation.

## Error handling and recovery

`recover()` no longer accepts an `on_recover` callback and no longer converts
exceptions into responses. Raise `HTTPException` for expected HTTP errors and
configure an application error handler when custom rendering is needed:

```python
@app.error_handler
async def error_handler(ctx: Context, exc: Exception) -> Response:
    return Response("request failed", status_code=500)
```

The application owns the final error-to-response boundary. Error handlers may
be synchronous or asynchronous.

## Duplicate registrations

Duplicate route names and duplicate method/path registrations now raise
`ValueError` during registration. Resolve collisions before starting the
application rather than relying on last-registration-wins behavior.

## WebSocket tests

An ASGI server sends the initial `websocket.connect` event before a WebSocket
handler runs. Direct ASGI tests must include that event; the handler still owns
the `accept()` call.
