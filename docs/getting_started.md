---
title: Getting Started
---

# Getting started

This guide takes you from an empty directory to a small JSON API. It assumes
Python 3.12 or newer and a working `uv` installation.

## 1. Create the project

```bash
mkdir lettia-demo
cd lettia-demo
uv init
uv add lettia uvicorn
uv add --dev pytest pytest-asyncio
```

Create `app.py`:

```python
from lettia import App, Context

app = App()


@app.get("/health")
def health(ctx: Context) -> dict[str, str]:
    return {"status": "ok"}


@app.get("/users/:user_id")
def get_user(ctx: Context) -> dict[str, str]:
    return {
        "id": ctx.path_params["user_id"],
        "name": "Ada",
    }
```

Start it:

```bash
uv run uvicorn app:app --reload
```

Try the endpoints:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/users/42
```

## 2. Add a JSON request

`Context.json()` reads and caches the ASGI request body. Invalid JSON is
reported as HTTP 400 by the framework.

```python
@app.post("/echo")
async def echo(ctx: Context) -> dict[str, object]:
    return {"received": await ctx.json()}
```

```bash
curl -X POST http://127.0.0.1:8000/echo \
  -H "content-type: application/json" \
  -d '{"message":"hello"}'
```

## 3. Add middleware

Register global middleware with `app.use()`. Register a middleware that must
run before route matching with `app.use_pre()`.

```python
from lettia.middleware import cors, recover, request_id, request_logger

app.use(
    recover(),
    request_logger(),
    cors(allow_origins=["http://localhost:3000"]),
    request_id(),
)
```

Middleware is applied in the order it is passed to `app.use()`, with the first
middleware becoming the outermost wrapper. See the
[middleware guide](middleware.md) for ordering and built-in middleware.

## 4. Add a typed payload

For an `attrs` class or dataclass, `ctx.bind()` combines JSON fields and query
parameters, then performs basic type coercion.

```python
from attrs import define


@define(slots=True)
class CreateUser:
    name: str
    age: int = 18


@app.post("/users")
async def create_user(ctx: Context) -> dict[str, object]:
    user = await ctx.bind(CreateUser)
    return {"name": user.name, "age": user.age}
```

Use the [context and binding guide](context_and_binding.md) for Pydantic,
dataclass, validation, response cookies, and streaming.

## 5. Test the application

For synchronous tests, use `TestClient`:

```python
from lettia.testing import TestClient


def test_health() -> None:
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

Run the suite:

```bash
uv run pytest
```

Continue with [testing](testing.md) for async tests, regression tests, and
coverage.

## Next steps

- Learn the route syntax and precedence in [Routing](routing.md).
- Understand the request lifecycle in [Architecture](architecture.md).
- Add production middleware with [Middleware](middleware.md).
- Add a WebSocket endpoint with [WebSockets](websocket.md).
- Look up exact signatures in the [API reference](api_reference.md).
