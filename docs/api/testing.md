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

```python
def test_health() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

## Async ASGI tests

Use HTTPX directly when a test already runs inside an event loop or needs
direct protocol control:

```python
async with httpx.AsyncClient(
    transport=httpx.ASGITransport(app=app),
    base_url="http://testserver",
) as client:
    response = await client.post("/echo", json={"message": "hello"})
```

This style is preferred for streaming and async HTTP tests. For lifespan,
custom `receive` / `send`, and WebSocket protocol tests, call the ASGI
application directly with controlled ASGI callables; HTTPX's ASGI transport
does not provide a WebSocket client.

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

The repository quality commands are:

```bash
uv run pytest
uv run ruff check .
uv run pyright
uv run pyrefly check
```

Pytest is configured with a 90% source-coverage gate. Coverage should support
behavioral confidence rather than become a line-count target.
