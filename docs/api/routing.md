---
title: Routing API
---

# Routing API

The routing subsystem consists of three public components:

- `Route`: immutable route metadata;
- `Router`: method-aware matching and reverse URL generation; and
- `Group`: application-bound prefix and middleware composition.

For usage patterns and precedence examples, see the [Routing guide](../routing.md).

## `Route`

```python
@define(slots=True, frozen=True)
class Route:
    method: str
    path: str
    handler: Callable[..., Any]
    name: str | None = None
```

`Route` is an immutable record. `method` is stored in uppercase by
`Router.add_route()`. Route objects are returned from `Router.add_route()` and
are also stored internally for dispatch.

## `Router`

```python
router = Router()
```

### `add_route()`

```python
router.add_route(
    method: str,
    path: str,
    handler: Any,
    name: str | None = None,
) -> Route
```

Static paths are stored in a direct lookup table. Paths containing `:` or `*`
are inserted into the radix tree.

Supported path forms are:

| Form | Captured value |
|---|---|
| `/users` | No parameters |
| `/users/:user_id` | One segment under `user_id` |
| `/files/*filepath` | The remaining path under `filepath` |

### `match()`

```python
router.match(
    method: str,
    path: str,
) -> tuple[Route, dict[str, str]] | None
```

The matcher checks static routes first, then parameter branches, then wildcard
branches. `HEAD` falls back to the matching `GET` route when no explicit HEAD
route exists. Parameter values are returned as strings.

### `allowed_methods()`

```python
router.allowed_methods(path: str) -> set[str]
```

Returns methods registered at a path, including methods reachable through the
radix tree. `App` uses this to produce a 405 response and an `Allow` header
when the path exists but the requested method does not.

### `url_for()`

```python
router.url_for(name: str, **kwargs: Any) -> str
```

Named parameters are replaced and URL-encoded. Wildcard values preserve `/` so
that nested paths remain nested. Missing or unexpected arguments are errors;
the method does not silently return a partially substituted path.

```python
router.add_route("GET", "/users/:user_id", handler, name="user")
router.url_for("user", user_id="a user")
# "/users/a%20user"
```

## `Group`

```python
group = app.group(prefix: str, *middlewares: Middleware)
```

`Group` is bound to an `App`; it does not own a separate router. Its methods
are:

```python
group.use(*middlewares: Middleware) -> Group
group.group(prefix: str, *middlewares: Middleware) -> Group
group.add_route(method, path, handler, name=None, middlewares=None) -> None
group.get(path, name=None)
group.post(path, name=None)
group.put(path, name=None)
group.delete(path, name=None)
group.patch(path, name=None)
```

When a route is added, the group prefix is joined to the route path and
inherited middleware is placed before route-specific middleware.

```python
api = app.group("/api/v1", auth)
users = api.group("/users", audit)


@users.get("/:user_id")
def detail(ctx: Context) -> dict[str, str]:
    return {"id": ctx.path_params["user_id"]}
```

The resulting path is `/api/v1/users/:user_id`, and the middleware order is
`auth`, then `audit`, then any route-specific middleware.
