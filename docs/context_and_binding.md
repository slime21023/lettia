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
| `ctx.state` | Context | Per-request typed state |

```python
@app.get("/search")
async def search(ctx: Context):
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
413, including when a previous middleware has already cached it. The strictest
limit supplied so far remains active for later reads, and a rejected body stays
rejected. A client disconnect or malformed JSON/text body raises HTTP 400. The
`body_limit()` middleware is useful when every route needs the same limit.
JSON numbers exceeding Python's integer-decoding length limit also return 400.
JSON nesting beyond the decoder or validation recursion limit returns 400
through both `ctx.json()` and binding. This limit is independent of body size
and depends on the Python runtime and current call depth.

Use these managed body APIs when returning a stream: the disconnect monitor
shares the Context cache. Raw `ctx.receive` reads bypass that coordination and
must not compete with it. See the [body API contract](api/context.md#body-api).

## Binding typed input

`ctx.bind(TargetClass)` reads JSON object fields for POST, PUT and PATCH, fills
missing keys from query parameters, and
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
async def create_user(ctx: Context):
    user = await ctx.bind(CreateUser)
    return {"name": user.name, "age": user.age}
```

Attrs/dataclass fields support `str`, `int`, `float`, `bool`, and nullable forms
such as `int | None`. JSON null is accepted only for nullable fields, and string
fields reject non-strings. Numeric strings and boolean strings (`true`, `false`,
`1`, `0`, `yes`, `no`, case-insensitive) are converted; booleans are not numbers.
Invalid values or missing required fields produce HTTP 400. Unsupported
annotations (including collections and nested models) raise configuration
`TypeError`, even if that field is absent from the input. Use PydanticBinder
for these models.

JSON object fields take precedence over query parameters. Unknown input fields
are ignored by the basic binders; omitted fields keep constructor defaults.
Only constructor fields participate: `init=False` fields are excluded, including
their annotations. Attrs input names follow constructor aliases: `_name: str`
accepts `name`, and `field(alias="years")` accepts `years` rather than the field
name. Dataclasses use their constructor field names, including `InitVar[T]`
parameters passed to `__post_init__()`. These use the same scalar and nullable
rules as `T`; inherited parameters, keyword-only parameters, and defaults are
preserved. Unsupported `InitVar` types are configuration errors.
Integer-to-float overflow
is invalid input and returns 400, while unsupported constructor annotations
remain configuration errors.

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
from pydantic import BaseModel, Field


class CreateUserSchema(BaseModel):
    name: str = Field(min_length=1)
    age: int = Field(ge=0)


@app.post("/validated-users")
async def create_validated_user(ctx: Context):
    user = await ctx.bind(CreateUserSchema)
    return {"data": user.model_dump()}
```

An empty name or negative age produces HTTP 400 through Pydantic validation.
Programming errors in a validator, such as `TypeError` or `ImportError`, retain
their original exception and normally produce HTTP 500 through App. They do
not select a different binder. See the
[Binder error table](api/extensions.md#binder-protocol) for the distinction
between input errors, model configuration and direct binder calls.

## State and aborts

Use `ctx.state` to pass request-local values between middleware and handlers.
Keys carry the value type and are identity-based, so packages cannot collide by
accidentally reusing the same string:

```python
from lettia import StateKey

CURRENT_USER = StateKey[str]("myapp.current_user")

ctx.state.set(CURRENT_USER, "user-123")
user_id = ctx.state.require(CURRENT_USER)
```

Use `ctx.abort()` for expected HTTP failures:

```python
if ctx.header("authorization") is None:
    ctx.abort(401, "Authentication required")
```

`ctx.state.get(CURRENT_USER)` returns `str | None`; `require()` raises
`KeyError` when the key was not set, and `discard()` removes a value. Use
`REQUEST_ID` and `SESSION` for values published by Lettia's built-in
middleware. Application-wide resources must be passed explicitly through an
app factory, not stored on `App`. A `StateKey` is identity-based: two keys with
the same display name remain distinct, which prevents accidental collisions
between integrations.

## Post-response tasks

`ctx.add_background_task()` queues a synchronous or asynchronous callable to
run after response delivery and cleanup are eligible for completion work:

```python
import logging

logger = logging.getLogger(__name__)


def record_signup(name: str) -> None:
    logger.info("Created user %s", name)


@app.post("/signup")
async def signup(ctx: Context) -> dict[str, str]:
    user = await ctx.bind(CreateUser)
    ctx.add_background_task(record_signup, user.name)
    return {"status": "accepted"}
```

This example reuses the attrs `CreateUser` model above. Tasks run in the current
application call, including after successfully delivered error responses.
Observed disconnects and failed transmission or cleanup suppress them; task
exceptions are logged. See [background-task reliability](deployment.md#background-tasks-and-reliability)
before using them for work that must survive a process failure.

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
