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
uv init --bare --python 3.12
uv add lettia uvicorn
uv add --dev "lettia[testing]" pytest pytest-asyncio
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

The synchronous handlers above do short in-memory work. They run directly on
the event-loop thread, without automatic thread-pool offloading. Use async
handlers and awaitable operations for I/O; declaring a handler `async` alone
does not make blocking calls nonblocking.

## 2. Add a JSON request

Append this route to `app.py`. `ctx.json()` returns the decoded request data;
invalid JSON produces HTTP 400.

```python
from lettia import JSONValue


@app.post("/echo")
async def echo(ctx: Context) -> dict[str, JSONValue]:
    return {"received": await ctx.json()}
```

```bash
curl -X POST http://127.0.0.1:8000/echo \
  -H "content-type: application/json" \
  -d '{"message":"hello"}'
```

The response is HTTP 200 with `{"received": {"message": "hello"}}`.
The multi-line command above uses POSIX shell syntax. In PowerShell, use:

```powershell
Invoke-RestMethod -Uri http://127.0.0.1:8000/echo -Method Post -ContentType application/json -Body '{"message":"hello"}'
```

## 3. Add middleware

Register global middleware with `app.use()`. Register a middleware that must
run before route matching with `app.use_pre()`.

```python
from lettia.middleware import body_limit, cors, request_id, request_logger

app.use(
    request_id(),
    request_logger(),
    cors(allow_origins=["http://localhost:3000"]),
    body_limit(max_bytes=1024 * 1024),
)
```

Middleware is applied in the order it is passed to `app.use()`, with the first
middleware becoming the outermost wrapper. See the
[middleware guide](middleware.md) for ordering and built-in middleware.

## 4. Add a typed payload

Add the model and route below to `app.py`. For POST, PUT and PATCH,
`ctx.bind()` combines JSON object fields with query parameters; JSON wins when
both supply the same field. Other methods use query parameters.

```python
from attrs import define


@define(slots=True)
class CreateUser:
    name: str
    age: int = 18


@app.post("/users")
async def create_user(ctx: Context) -> dict[str, str | int]:
    user = await ctx.bind(CreateUser)
    return {"name": user.name, "age": user.age}
```

Use the [context and binding guide](context_and_binding.md) for Pydantic,
dataclass, validation, response cookies, and streaming.

| POST `/users` JSON body | Expected result |
|---|---|
| `{"name": "Ada"}` | 200, `{"name": "Ada", "age": 18}` |
| `{"name": "Ada", "age": "21"}` | 200, with integer `age: 21` |
| `{"age": 21}` | 400 because `name` is required |
| `{"name": "Ada", "age": "invalid"}` | 400 because `age` cannot be converted |

Basic binding converts scalar types; it does not impose business rules such as
a minimum age. Use Pydantic constraints or explicit validation for those rules.

## 5. Test the application

Create `test_app.py` alongside `app.py`. The `lettia[testing]` extra installed
above provides the HTTPX dependency used by `TestClient`; no server is needed.

```python
from lettia.testing import TestClient
from app import app


def test_health() -> None:
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_user_rejects_invalid_age() -> None:
    response = TestClient(app).post("/users", json={"name": "Ada", "age": "bad"})
    assert response.status_code == 400
```

Run the suite:

```bash
uv run python -m pytest
```

Continue with [testing](testing.md) for async tests, regression tests, and
coverage.

Expected result: both tests pass. If importing the test client reports that
HTTPX is missing, install `lettia[testing]` in the environment running pytest.
If a route returns 404, check that its module is imported before serving `app`.

## Next steps

- Learn the route syntax and precedence in [Routing](routing.md).
- Understand the request lifecycle in [Architecture](architecture.md).
- Add production middleware with [Middleware](middleware.md).
- Configure the ASGI server and operational boundaries with [Deployment](deployment.md).
- Add a WebSocket endpoint with [WebSockets](websocket.md).
- Look up exact signatures in the [API reference](api_reference.md).
