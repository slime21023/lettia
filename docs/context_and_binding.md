---
title: Context and Binding
---

# Context and binding

`Context` is the object passed to every HTTP handler. It exposes request data,
route parameters, per-request state, typed binding, and response-adjacent
helpers without hiding the underlying ASGI request.

## Request data at a glance

| Accessor | Source | Notes |
|---|---|---|
| `ctx.path` | ASGI `scope["path"]` | Writable by pre-routing middleware |
| `ctx.method` | ASGI `scope["method"]` | Uppercase HTTP method |
| `ctx.path_params` | Router | `dict[str, str]` populated after matching |
| `ctx.query_params` | ASGI `query_string` | Lazy `dict[str, list[str]]` |
| `ctx.headers` | ASGI `headers` | Lazy, lower-case keys |
| `ctx.cookies` | `Cookie` header | Lazy cookie mapping |
| `ctx.state` | Application | Per-request mutable state |

```python
@app.get("/search")
async def search(ctx: Context) -> dict[str, object]:
    return {
        "query": ctx.query_param("q", default=""),
        "agent": ctx.header("user-agent"),
        "session": ctx.cookie("session"),
        "tags": ctx.query_params.get("tag", []),
    }
```

Query strings, headers, and cookies are parsed on first access and cached for
the rest of the request.

## Reading the body

```python
raw_body = await ctx.body(max_bytes=1024 * 1024)
payload = await ctx.json()
plain_text = await ctx.text()
```

The body is read once and cached. A body that exceeds `max_bytes` raises HTTP
413; a client disconnect or malformed JSON/text body raises HTTP 400. The
`body_limit()` middleware is useful when every route needs the same limit.

## Binding typed input

`ctx.bind(TargetClass)` combines JSON object fields with query parameters and
constructs one of the supported target types:

- An `attrs` class.

- A standard-library `dataclass`.

- A Pydantic v2 model when the optional dependency is installed.

### attrs

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

Invalid primitive values produce HTTP 400 instead of silently leaving the
wrong type in the model.

### Dataclass

```python
from dataclasses import dataclass


@dataclass
class Profile:
    display_name: str
    active: bool = True
```

Use `await ctx.bind(Profile)` in the same way as the `attrs` example.

### Pydantic

Install the optional dependency first:

```bash
uv add "lettia[pydantic]"
```

```python
from pydantic import BaseModel, EmailStr


class CreateUserSchema(BaseModel):
    name: str
    email: EmailStr


@app.post("/validated-users")
async def create_validated_user(ctx: Context) -> dict[str, object]:
    user = await ctx.bind(CreateUserSchema)
    return {"data": user.model_dump()}
```

## State and aborts

Use `ctx.state` to pass request-local values between middleware and handlers:

```python
ctx.state["request_id"] = "request-123"
```

Use `ctx.abort()` for expected HTTP failures:

```python
if ctx.header("authorization") is None:
    ctx.abort(401, "Authentication required")
```

Use `app.state` for application-wide resources; it is a dictionary and is not
shared with `ctx.state`.

## Post-response tasks

`ctx.add_background_task()` queues a synchronous or asynchronous callable to
run after the response messages have been sent:

```python
def record_signup(email: str) -> None:
    audit_log.write(email)


@app.post("/signup")
async def signup(ctx: Context) -> dict[str, str]:
    payload = await ctx.json()
    ctx.add_background_task(record_signup, payload["email"])
    return {"status": "accepted"}
```

These tasks are part of the current application call and are logged if they
fail. Use a real queue or worker for durable, long-running work.

## Responses, cookies, and streams

Return a value directly for simple responses. Use an explicit response when
you need headers, cookies, status codes, or streaming:

```python
from lettia import JsonResponse


@app.post("/login")
def login(ctx: Context) -> JsonResponse:
    response = JsonResponse({"status": "ok"})
    response.set_cookie("session", "signed-value", httponly=True, secure=True)
    return response
```

Use `response.delete_cookie("session")` to expire a cookie. For large payloads
or SSE, return a `StreamResponse` backed by an async generator. See the
[API reference](api_reference.md) for response signatures.
