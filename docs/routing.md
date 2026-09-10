---
title: Routing
---

# Routing

Routes map an HTTP method and path pattern to a callable. A handler receives a
`Context` and may be synchronous or asynchronous.

## Route syntax

| Pattern | Meaning | Example |
|---|---|---|
| `/health` | Exact static path | `/health` |
| `/users/:user_id` | One dynamic path segment | `/users/42` |
| `/files/*filepath` | Remainder of the path | `/files/css/app.css` |

Static routes use a direct lookup. Parameterized and wildcard routes use the
radix tree. The matching precedence is:

1. Exact static route.

2. Parameterized route.

3. Wildcard route.

```python
from lettia import App, Context

app = App()


@app.get("/users")
def list_users(ctx: Context) -> list[str]:
    return ["Ada", "Grace"]


@app.get("/users/:user_id")
def get_user(ctx: Context) -> dict[str, str]:
    return {"id": ctx.path_params["user_id"]}


@app.get("/files/*filepath")
def get_file(ctx: Context) -> dict[str, str]:
    return {"path": ctx.path_params["filepath"]}
```

`ctx.path_params` contains decoded route values as strings. A missing path
returns 404. A known path with an unsupported method returns 405 and includes
an `Allow` header. `HEAD` uses the matching `GET` route unless an explicit
`HEAD` route is registered.

Registering the same method and path, or reusing a route name, raises
`ValueError`.

## Registering routes

Use decorators for the common case:

```python
@app.post("/users")
async def create_user(ctx: Context):
    return {"created": await ctx.json()}
```

Use `add_route()` when the handler or route metadata is assembled dynamically:

```python
app.add_route("GET", "/health", health, name="health")
```

Supported application decorators are `get`, `post`, `put`, `delete`, `patch`,
and `websocket`.

## Groups and route middleware

Groups compose a prefix and route middleware. Nested groups inherit both:

```python
from lettia import App, Context

app = App()
api = app.group("/api/v1", auth_middleware)
users = api.group("/users")


@users.get("/:user_id")
def get_user_detail(ctx: Context) -> dict[str, str]:
    return {"id": ctx.path_params["user_id"]}
```

The final path is `/api/v1/users/:user_id`, and `auth_middleware` runs for
that route. Route-level middleware can also be passed to `add_route()`.

## Reverse URL lookup

Name a route to generate a path later:

```python
@app.get("/users/:user_id", name="user_detail")
def user_detail(ctx: Context) -> dict[str, str]:
    return {"id": ctx.path_params["user_id"]}


url = app.url_for("user_detail", user_id="a user")
# "/users/a%20user"
```

Parameterized values are URL-encoded. Wildcard values preserve `/` so nested
paths remain nested. Missing parameters raise `KeyError`; unexpected
parameters raise `TypeError` instead of silently producing a partial URL.
