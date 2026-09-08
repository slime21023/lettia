---
title: Testing
---

# Testing

Lettia supports two useful testing styles:

- `TestClient` for concise synchronous tests.

- `httpx.AsyncClient` with `ASGITransport` for async HTTP tests.

- Direct `app(scope, receive, send)` calls for lifespan and WebSocket protocol
  tests.

Install the development tools:

```bash
uv add --dev pytest pytest-asyncio pytest-cov
```

## Synchronous route tests

`TestClient` wraps `httpx.ASGITransport` and exposes `get`, `post`, `put`,
`delete`, and `patch` helpers:

```python
from lettia import App, Context
from lettia.testing import TestClient

app = App()


@app.get("/users/:user_id")
def get_user(ctx: Context) -> dict[str, str]:
    return {"id": ctx.path_params["user_id"]}


def test_get_user() -> None:
    client = TestClient(app)

    response = client.get("/users/42")

    assert response.status_code == 200
    assert response.json() == {"id": "42"}
```

`TestClient` is synchronous and uses `asyncio.run()` internally. Use the async
style below when the test itself already runs inside an event loop.

## Async ASGI tests

```python
import httpx
import pytest


@pytest.mark.asyncio
async def test_echo() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post("/echo", json={"message": "hello"})

    assert response.status_code == 200
```

Use this style for streaming and async HTTP behavior. Test lifespan and
WebSocket protocol messages by calling the ASGI application with controlled
`scope`, `receive`, and `send` callables; HTTPX's ASGI transport is an HTTP
transport, not a WebSocket client.

## What to test

Organize tests around public behavior rather than private implementation:

- A route returns the expected status, headers, and body.

- Invalid input returns the documented HTTP error.

- Middleware preserves nesting order and error semantics.

- Route precedence, path parameters, 405/Allow, and HEAD behavior are stable.

- Static file traversal, range requests, cookies, and WebSocket disconnects
  are covered as boundary cases.

- The suite enforces combined statement and branch coverage, so conditional
  error paths remain part of the quality gate.

Name tests as `test_<unit>_<scenario>_<expected_result>()` so failures explain
the contract they protect.

## Coverage and quality gates

Run the complete development loop:

```bash
uv run pytest
uv run ruff check .
uv run pyright
uv run pyrefly check
```

Pytest is configured to print missing lines and fail below 90% source coverage.
For a focused report:

```bash
uv run pytest tests/test_regressions.py --cov=lettia --cov-report=term-missing
```

Coverage is a diagnostic, not a target by itself. Prefer tests that protect
observable behavior and edge conditions over tests written only to execute a
line.
