---
title: Testing
---

# Testing

This page starts with tests for your application. You can test routes without
starting a server or adopting Lettia's own repository configuration. Framework
contributors can use [framework quality checks](contributing_testing.md).

Choose a test transport according to the behavior being checked:

- `TestClient` for concise synchronous tests.

- `httpx.AsyncClient` with `ASGITransport` for async HTTP tests.

- Direct `app(scope, receive, send)` calls for lifespan and WebSocket protocol
  tests, or controlled HTTP send/receive failures.

- A real Uvicorn server and HTTPX client for socket disconnects, connection
  reuse and server startup/shutdown; see
  [framework smoke tests](contributing_testing.md#real-server-smoke-tests).

In your application project, install the testing extra and pytest:

```bash
uv add --dev "lettia[testing]" pytest pytest-asyncio
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
style below when the test itself already runs inside an event loop. Each
request creates a new HTTPX client: response cookies are not carried to the
next request, even when you reuse the same `TestClient`. Neither test style
runs startup/shutdown hooks automatically.
Save the example as `test_users.py` and run `uv run python -m pytest`. In an
existing application, import its `app` or call its application factory instead
of defining routes inside the test file.

## Async ASGI tests

HTTPX describes its ASGI callable more broadly than Lettia's typed scopes. The
cast below adapts that boundary for strict type checkers; it does not wrap or
change the application at runtime. Save this example as `test_echo.py`.

```python
from collections.abc import Awaitable, Callable
from typing import cast

import httpx
import pytest

from lettia import App, Context, JSONValue

app = App()


@app.post("/echo")
async def echo(ctx: Context) -> dict[str, JSONValue]:
    return {"received": await ctx.json()}


@pytest.mark.asyncio
async def test_echo() -> None:
    transport = httpx.ASGITransport(app=cast(Callable[..., Awaitable[None]], app))
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        response = await client.post("/echo", json={"message": "hello"})

    assert response.status_code == 200
    assert response.json() == {"received": {"message": "hello"}}
```

Use this style for application HTTP behavior. In-process transports can buffer
streams and cannot verify real client disconnects; use the socket smoke suite
for those boundaries. HTTPX ASGITransport does not run lifespan automatically.
Test lifespan and WebSocket protocol messages by calling the ASGI application with controlled
`scope`, `receive`, and `send` callables; HTTPX's ASGI transport is an HTTP
transport, not a WebSocket client.

## Cookie flows across requests

Use one `httpx.AsyncClient` for a sequence that needs a cookie jar. Save this
complete example as `test_preferences.py`; it uses the testing dependencies
installed above. The cookie stores a display preference, not authentication.

```python
from collections.abc import Awaitable, Callable
from typing import cast

import httpx
import pytest

from lettia import App, Context, JsonResponse

app = App()


@app.post("/preferences")
def save_preferences(ctx: Context) -> JsonResponse:
    response = JsonResponse({"saved": True})
    response.set_cookie("theme", "dark", httponly=True)
    return response


@app.get("/preferences")
def read_preferences(ctx: Context) -> dict[str, str | None]:
    return {"theme": ctx.cookie("theme")}


@pytest.mark.asyncio
async def test_cookie_is_sent_on_the_next_request() -> None:
    transport = httpx.ASGITransport(app=cast(Callable[..., Awaitable[None]], app))
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        saved = await client.post("/preferences")
        response = await client.get("/preferences")

    assert saved.status_code == 200
    assert response.json() == {"theme": "dark"}
```

Run `uv run python -m pytest test_preferences.py`; the test should pass.
For a single synchronous request, cookies can instead be supplied explicitly
through `client.request("GET", "/preferences", cookies={"theme": "dark"})`.
The convenience `get()` method does not accept a `cookies` argument.

## Choosing the test boundary

| Behavior | Test approach | Limitation |
|---|---|---|
| One route or middleware response | `TestClient` | No persistent cookie jar or lifespan |
| Async tests and consecutive cookie requests | One HTTPX `AsyncClient` with `ASGITransport` | No automatic lifespan or real socket disconnect |
| Startup/shutdown and WebSocket messages | Controlled ASGI calls | The test must drive the protocol |
| Actual disconnect, streaming and server startup | A real ASGI server and HTTP client | Manage server readiness and cleanup |

Use `client.request("HEAD", path)` or `client.request("OPTIONS", path)` for
methods without convenience helpers. HTTP clients do not enforce browser CORS
rules: header assertions test the server policy; verify browser behavior with
your frontend as well.

## What to test

Organize tests around public behavior rather than private implementation:

- A route returns the expected status, headers, and body.

- Invalid input returns the documented HTTP error.

- Middleware preserves nesting order and error semantics.

- Route precedence, path parameters, 405/Allow, and HEAD behavior are stable.

- Static file traversal, range requests, cookies, and WebSocket disconnects
  are covered as boundary cases.

- Cover both successful requests and error paths; select a coverage policy
  appropriate to your application.

Name tests as `test_<unit>_<scenario>_<expected_result>()` so failures explain
the contract they protect.


<span id="property-based-core-tests"></span>
<span id="run-and-reproduce"></span>
<span id="coverage-and-quality-gates"></span>
<span id="contracts-and-tdd"></span>
<span id="real-server-smoke-tests"></span>
<span id="verification-commands"></span>
<span id="validation-records"></span>
<span id="historical-property-test-migration"></span>

## Framework contributor checks

The former contributor sections have moved to
[Framework testing and quality checks](contributing_testing.md). Use its
[verification commands](contributing_testing.md#verification-commands),
[contract and TDD workflow](contributing_testing.md#contracts-and-tdd), and
[validation records](contributing_testing.md#validation-records) when maintaining
Lettia. These are not application setup requirements.
