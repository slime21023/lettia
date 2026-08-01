---
title: API Reference Overview
---

# API reference

This reference is organized by runtime component. Each page describes the
component's public contract, lifecycle role, method signatures, return values,
error behavior, and extension boundaries.

Use the topic guides for first-principles learning and runnable workflows. Use
these API pages when you are integrating Lettia into a larger system,
implementing an adapter, or reasoning about protocol behavior.

## Component map

| Component | API reference | What it owns |
|---|---|---|
| Application | [App](api/app.md) | ASGI entrypoint, registration, lifecycle, errors, state |
| Routing | [Routing](api/routing.md) | `Route`, `Router`, `Group`, matching, reverse URLs |
| Request context | [Context](api/context.md) | Request data, body, binding, state, aborts |
| Responses | [Responses](api/response.md) | Response types, cookies, streams, ASGI writing |
| Middleware | [Middleware](api/middleware.md) | Handler contracts, chain construction, built-ins |
| WebSockets | [WebSocket](api/websocket.md) | Connection state, frames, accept/close lifecycle |
| Extensions | [Protocols and extensions](api/extensions.md) | Binders, validators, renderers, static files |
| Testing | [Testing](api/testing.md) | `TestClient`, ASGI transport, test contracts |

## Read the system in order

For advanced development, the recommended sequence is:

1. Read [Architecture](architecture.md) to understand the ASGI boundaries and
   request lifecycle.

2. Read [App](api/app.md) to understand how the application composes routes,
   middleware, errors, and lifespan.

3. Read [Routing](api/routing.md), [Context](api/context.md), and
   [Responses](api/response.md) to follow data from route match to ASGI output.

4. Read [Middleware](api/middleware.md) and
   [Protocols and extensions](api/extensions.md) before implementing custom
   cross-cutting behavior or adapters.

5. Read [WebSocket](api/websocket.md) and [Testing](api/testing.md) for the
   non-HTTP branch and protocol-level verification.

## Public API conventions

The following conventions apply across the public API:

- Public classes and functions are exported from `lettia`,
  `lettia.middleware`, `lettia.protocols`, `lettia.ext`, or `lettia.testing`.

- HTTP handlers may be sync or async; middleware handlers are async callables.

- Expected HTTP failures use `HTTPException` / `abort()` and preserve status,
  detail, and headers through the application error handler.

- Request and connection state are mutable dictionaries scoped to one context;
  application-wide resources belong in `app.state`.

- The framework keeps WebSocket dispatch separate from HTTP middleware and
  response normalization.

## Source orientation

The implementation maps directly to the API pages:

```text
src/lettia/app.py          -> App
src/lettia/router.py       -> Router
src/lettia/route.py        -> Route
src/lettia/group.py        -> Group
src/lettia/context.py      -> Context
src/lettia/response.py     -> Response and ResponseWriter
src/lettia/websocket.py    -> WebSocketContext and state
src/lettia/middleware/     -> Middleware factories
src/lettia/protocols/      -> Binder, Validator, Renderer
src/lettia/ext/static.py   -> StaticFiles
src/lettia/testing.py      -> TestClient
```
