---
title: Testing API
---

# Testing API

Lettia testing is intentionally close to ASGI. The synchronous helper is a
thin wrapper around HTTPX; advanced tests can call the application directly.

## `TestClient`

```python
TestClient(app: App, base_url: str = "http://testserver")
```

The client uses `httpx.ASGITransport` internally and exposes:

```python
client.request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
    content: str | bytes | None = None,
    json: JSONValue | None = None,
    params: Mapping[str, QueryValue] | None = None,
) -> httpx.Response

client.get(url, *, headers=None, params=None)
client.post(url, *, headers=None, json=None, content=None)
client.put(url, *, headers=None, json=None, content=None)
client.delete(url, *, headers=None)
client.patch(url, *, headers=None, json=None)
```

The convenience methods call `request()` and return normal `httpx.Response`
objects.

The helper runs an in-process ASGI transport, not a network server, and does not
automatically run lifespan. It uses `asyncio.run()` internally, so use an async
client when the caller already runs inside an event loop. Each request creates
a new HTTPX client, so response cookies are not persisted across requests.
Pass `cookies` to `request()` explicitly or use one async client as shown in
the [cookie workflow](../testing.md#cookie-flows-across-requests). Use
`request("HEAD", path)` and `request("OPTIONS", path)` for methods without
convenience helpers.

```python
def test_health() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

## Async ASGI tests

Use HTTPX directly when a test already runs inside an event loop or needs
consecutive HTTP requests. This fragment belongs inside an async test and
assumes an existing `app` with an `/echo` route. The cast adapts HTTPX's broad
ASGI callable type for static checking; see the
[complete example](../testing.md#async-asgi-tests).

```python
from collections.abc import Awaitable, Callable
from typing import cast

import httpx

transport = httpx.ASGITransport(app=cast(Callable[..., Awaitable[None]], app))
async with httpx.AsyncClient(
    transport=transport,
    base_url="http://testserver",
) as client:
    response = await client.post("/echo", json={"message": "hello"})
```

Use this style for async application HTTP behavior. ASGITransport can buffer
streamed output; it does not verify wire-level streaming or client disconnects.
Use [real-server smoke tests](../contributing_testing.md#real-server-smoke-tests) for those
boundaries. For lifespan, controlled HTTP failures and WebSocket protocol tests,
call the ASGI application directly with controlled `receive` / `send` callables.
HTTPX's ASGI transport does not run lifespan or provide a WebSocket client.

## Testing contracts

Tests should protect public behavior:

| Area | Important contracts |
|---|---|
| Routing | Precedence, path parameters, 404/405, `Allow`, and HEAD |
| Middleware | Nesting order, scope, state propagation, error semantics |
| Context | Lazy parsing, body limits, invalid input, binding |
| Responses | Headers, cookies, status, streaming, and normalization |
| Extensions | Traversal protection, ranges, sessions, and rate limits |
| WebSockets | State transitions and normal disconnect behavior |

Use the [verification commands](../contributing_testing.md#verification-commands) for the
complete quality gates, checker scopes and optional ty check. Repository tests
are classified by unit, integration, smoke and architecture directories and
linked to contract IDs; see the [test pyramid](../contributing_testing.md#coverage-and-quality-gates).
Pytest enforces a 90% combined statement/branch coverage gate.
