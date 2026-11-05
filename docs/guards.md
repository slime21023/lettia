# Guards

A guard decides whether a request may reach an operation and, when it does, produces the *principal* (the authenticated caller) that the handler reads as `c.principal`. Guards are declared on the contract with `GuardSpec` and implemented where the secrets live with `spec.use(fn)`. Declaration and implementation are separate: the contract says which guard protects an operation, `App(guards=[...])` says how that guard works.

```python
from lettia import (
    ApiKeyHeader, App, GuardSpec, HttpBearer, RequestHead, public,
)
from lettia.core import Forbidden, Unauthorized
```

## Declaring a guard

```python
GuardSpec[G](name: str, *, scheme: SecurityScheme | None = None)
```

| Parameter | Meaning |
|---|---|
| `G` (type argument) | Principal type the guard produces. The handler's `Context[G]` must match; a mismatch is a type-checker error. |
| `name` | Identifies the guard in startup messages and is the security scheme name in the OpenAPI document. |
| `scheme` | How the guard appears in OpenAPI. Optional; without it the guard is not listed under `security`. |

Specs are compared by identity, not by name. The implementation registered for a spec is the one passed to that spec's `use`; two specs with the same name are different guards.

### Security schemes

Two schemes exist. Both are frozen dataclasses importable from `lettia`.

| Scheme | OpenAPI output |
|---|---|
| `HttpBearer(format: str \| None = None)` | `{"type": "http", "scheme": "bearer"}`, plus `"bearerFormat": format` when `format` is set |
| `ApiKeyHeader(name: str)` | `{"type": "apiKey", "in": "header", "name": name}` |

The scheme is documentation only. It does not make the guard read any header; the implementation does that. See [openapi.md](openapi.md) for how schemes become `securitySchemes` and `security`.

```python
class User(msgspec.Struct):
    name: str

class Admin(msgspec.Struct):
    level: int

class Key(msgspec.Struct):
    owner: str

bearer = GuardSpec[User]("bearer", scheme=HttpBearer(format="JWT"))
apikey = GuardSpec[Key]("apikey", scheme=ApiKeyHeader("X-API-Key"))
admin = GuardSpec[Admin]("admin")  # no scheme
```

## Implementing a guard

`spec.use(fn)` binds an implementation and returns a `GuardImpl`. `fn` is an async function taking a `RequestHead` and returning the principal:

```python
Callable[[RequestHead], Awaitable[G]]
```

```python
async def verify_bearer(head: RequestHead) -> User:
    if head.headers.get("authorization", "") != "Bearer good":
        raise Unauthorized(
            "a valid token is required", headers={"WWW-Authenticate": "Bearer"}
        )
    return User("ann")

async def verify_key(head: RequestHead) -> Key:
    if head.headers.get("x-api-key") != "k1":
        raise Unauthorized("bad key")
    return Key("acme")

async def require_admin(head: RequestHead) -> Admin:
    if head.headers.get("x-role") != "admin":
        raise Forbidden("administrators only")
    return Admin(1)
```

`use` also works as a decorator: `@bearer.use` on an `async def` rebinds the name to the `GuardImpl`.

A guard function receives a `RequestHead`: `method`, `path`, `raw_path`, `client`, and the lazily parsed `headers`, `query` and `cookies`. The body is not reachable from a guard. Guards run before the request is decoded, so an unauthenticated request with an invalid body gets 401, not 422.

`use` raises `TypeError` for a composite spec (`bearer | apikey`) and for `public`; only named guards are implemented.

## Assembling: `App(guards=[...])`

```python
app = App(guards=[
    bearer.use(verify_bearer),
    apikey.use(verify_key),
    admin.use(require_admin),
])
```

| Situation | Result |
|---|---|
| An item is not a `GuardImpl` (for example a bare `GuardSpec`) | `TypeError: guards must be made with spec.use(fn), got GuardSpec`, raised from `App(...)` |
| The same spec implemented twice | Startup problem: `guard 'bearer' is implemented more than once` (the first one is kept) |
| An operation uses a guard with no implementation | Startup problem per operation: `Api.o: guard 'other' has no implementation; bind one with guard.use(fn) and pass it to App(guards=[...])` |
| An implemented guard that no operation uses | Not an error |

Startup problems are collected and raised together as `StartupError` from `app.build()` (which the lifespan, the first request, `app.openapi()` and the `lettia openapi export` command trigger). `StartupError.problems` lists every message.

Guards registered on the `App` apply to contracts mounted on groups too (`app.group("/v1").mount(...)`).

## Using guards on operations

`guard=` is a required keyword argument of every `op.*` decorator. Use `public` for operations that need no authentication.

```python
from typing import Protocol
from lettia import Context, Ok, op

class Api(Protocol):
    @op.get("/me", guard=bearer)
    async def me(self, c: Context[User], req: None) -> Ok[str]: ...

    @op.get("/open", guard=public)
    async def open(self, c: Context[None], req: None) -> Ok[str]: ...
```

### `public`

`public` is a `GuardSpec[None]`. It is never run; the principal is `None`. It needs no implementation and cannot be passed to `use`.

### `c.principal`

The handler context is `Context[G]`; `c.principal` is typed `G`. For a `public` operation `G` is `None`.

```python
async def me(self, c: Context[User], req: None) -> Ok[str]:
    return Ok(c.principal.name)
```

## Composition

Guards compose with `|` (alternatives) and `&` (all required). The principal type is inferred from the parts, and the handler's `Context[...]` must use that type.

| Expression | Principal type | Runtime |
|---|---|---|
| `a \| b` | `A \| B` | Try `a`, then `b`; the first success wins and later branches do not run |
| `a & b` | `tuple[A, B]` | Run `a`, then `b`; the principal is `(principal_a, principal_b)`; the first failure aborts and later guards do not run |
| `a \| public` | `A \| None` | Optional authentication: if `a` refuses, the request proceeds anonymously with `None` |

Chaining:

- `a | b | c` is flattened into one node with three branches, tried in order; the type is `A | B | C`.
- `a & b & c` is **not** flattened. It is `(a & b) & c`, so the principal is nested: `tuple[tuple[A, B], C]`.
- `(a & b) | (a & c)` tries each whole conjunction in turn.

```python
class Api(Protocol):
    @op.get("/either", guard=bearer | apikey)
    async def either(self, c: Context[User | Key], req: None) -> Ok[str]: ...

    @op.get("/maybe", guard=bearer | public)
    async def maybe(self, c: Context[User | None], req: None) -> Ok[str]: ...

    @op.get("/admin", guard=bearer & admin)
    async def admin_only(
        self, c: Context[tuple[User, Admin]], req: None
    ) -> Ok[str]: ...

    @op.get("/triple", guard=bearer & admin & apikey)
    async def triple(
        self, c: Context[tuple[tuple[User, Admin], Key]], req: None
    ) -> Ok[str]: ...
```

Handlers unpack the principal accordingly:

```python
class Service(Api):
    async def either(self, c: Context[User | Key], req: None) -> Ok[str]:
        who = c.principal
        return Ok(who.name if isinstance(who, User) else who.owner)

    async def maybe(self, c: Context[User | None], req: None) -> Ok[str]:
        return Ok("anonymous" if c.principal is None else c.principal.name)

    async def triple(
        self, c: Context[tuple[tuple[User, Admin], Key]], req: None
    ) -> Ok[str]:
        (user, adm), key = c.principal
        return Ok(f"{user.name}:{adm.level}:{key.owner}")
```

A composite spec has a derived name, `(bearer | apikey)` or `(bearer & admin)`.

## Failure semantics

A guard says "no" by raising `Unauthorized` (401) or `Forbidden` (403), both from `lettia.core`:

```python
Unauthorized(detail: str | None = None, *, headers: Mapping[str, str] | None = None)
Forbidden(detail: str | None = None, *, headers: Mapping[str, str] | None = None)
```

The response is a `application/problem+json` body (`type`, `title`, `status`, `detail`) and the `headers` you passed are added to the response, so `www-authenticate` on a 401 reaches the client. Use 401 when the caller is not authenticated and 403 when the caller is known but not allowed.

Observed with the guards above:

| Request | Status | Notes |
|---|---|---|
| `GET /me` without a token | 401 | `www-authenticate: Bearer`, detail `a valid token is required` |
| `GET /either` with `x-api-key: k1` | 200 | `bearer` refused, `apikey` accepted |
| `GET /either` with nothing | 401 | both branches refused |
| `GET /maybe` with nothing | 200 | principal is `None` |
| `GET /admin` with a good token, no role | 403 | from `admin` |
| `GET /admin` with a role, no token | 401 | `bearer` fails first; `admin` never runs |

Rules:

- **`&`**: the first guard that raises ends the request with its own status and headers.
- **`|`**: only `Unauthorized` and `Forbidden` count as a refused branch; the next branch is tried. If every branch refuses, the response is the first `Forbidden` among the refusals if there is one (a known caller who is not allowed is more informative than "unauthenticated"); otherwise the first `Unauthorized`. That exception's own detail and headers are used.
- **Any other exception** (including other `HTTPError` subclasses such as `HTTPError(503, ...)`) is not a refusal. It aborts the whole guard at once, never falls through to the next `|` branch, and goes to the app's error handler like any handler error (an unexpected exception becomes a 500). A crashing verifier therefore cannot let a request through.

## Startup checks

Guard problems are reported by `app.build()` as a `StartupError`, together with all other startup problems:

```python
app = App(guards=[bearer.use(verify_bearer)])
app.mount(Service())      # uses apikey and admin too
app.build()               # StartupError
```

```
Api.either: guard 'apikey' has no implementation; bind one with guard.use(fn) and pass it to App(guards=[...])
Api.admin_only: guard 'admin' has no implementation; bind one with guard.use(fn) and pass it to App(guards=[...])
```

An operation with a missing guard implementation is not registered.

## Guards and middleware

| | Guard | Middleware |
|---|---|---|
| Declared | Per operation, in the contract (`guard=`) | On the app (`app.use`) or a group |
| Sees | `RequestHead` only (no body) | The whole request context |
| Produces | A typed principal for the handler (`c.principal`) | Wraps the handler; may change the response |
| Missing implementation | Startup error | n/a |
| In OpenAPI | Yes (`security`, automatic 401/403 responses) | No |

Middleware wraps the compiled operation handler, so it runs before the guard; guard errors are converted to responses inside the chain, and middleware sees them as ordinary responses. Use a guard for "who may call this operation and as whom"; use middleware for cross-cutting behavior such as logging or headers. See [core.md](core.md).

## WebSocket operations

`op.websocket(path, guard=...)` accepts the same guards. The guard runs before the handshake: if it raises `Unauthorized` or `Forbidden` (any `HTTPError`) the server answers the upgrade request with the problem response (status, headers and body as for HTTP) and the handler is never entered. Otherwise the handler runs with `c.principal` set. The guard sees a `RequestHead` built from the WebSocket scope (method `GET`, path, query string, headers, client), so tokens must come from headers, query or cookies. Details in [websocket.md](websocket.md).

```python
async with TestClient(app) as client:
    async with client.websocket_connect("/ws") as ws:   # no token
        print(ws.accepted, ws.response_status)          # False 401
```

## See also

- [contract.md](contract.md): `op`, requests and responses
- [openapi.md](openapi.md): how guards become `security` and 401/403 responses
- [websocket.md](websocket.md): WebSocket operations
- [testing.md](testing.md): `TestClient` and `make_context`
- [core.md](core.md): middleware and the error handler
