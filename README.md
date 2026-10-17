# lettia

A contract-first ASGI framework for Python 3.12+.

The API contract is a `typing.Protocol`. It is the single source of truth: your
implementation class inherits it, the OpenAPI 3.1 document is derived from it, and
your type checker reports any deviation while you edit. There is no code
generation anywhere.

```python
class UsersApi(Protocol):                      # 1. the contract
    @op.get("/users/{user_id}", guard=bearer)
    async def get_user(
        self, c: Context[Caller], req: GetUserReq
    ) -> Ok[UserOut] | NotFound[Problem]:
        """Get a single user."""
        ...


class UserService(UsersApi):                   # 2. the implementation
    async def get_user(self, c: Context[Caller], req: GetUserReq):
        user = self.users.get(req.user_id)
        return Ok(user) if user else NotFound(Problem(title="user not found"))


app = App(guards=[bearer.use(verify_token)])   # 3. the application
app.group("/v1").mount(UserService(users))
```

A missing method, a wrong signature, a handler that returns a response the
contract does not list, or a guard that does not match `Context[G]` are errors in
your editor, not in production. The complete, runnable version of this example is
[`examples/users_api.py`](examples/users_api.py), and the document derived from it
is [`examples/openapi.json`](examples/openapi.json).

## Install

```bash
pip install lettia                # the only dependency is msgspec
pip install 'lettia[test]'        # httpx, for lettia.testing.TestClient
pip install 'lettia[yaml]'        # PyYAML, for `lettia openapi export --format yaml`
```

Run an app with any ASGI server, for example `uvicorn examples.users_api:app`.

## What you get

**Contract.** `op.get/post/put/patch/delete` declare path, method and guard.
Request types are msgspec structs whose fields say where each value comes from:
`Path[int]`, `Query[bool]`, `Header[str]`, `Cookie[str]`, `Body[NewUser]`. Responses
are classes such as `Ok[T]`, `Created[T]`, `NoContent` and `NotFound[T]`; a subclass
can add response headers with `Annotated[str, HeaderName("Location")]`.

**Guards.** A guard is declared with the contract and implemented where the secrets
are. `GuardSpec[User]` decides the type of `c.principal`, and guards compose with
types inferred: `bearer | apikey` is `User | ApiKey`, `bearer & admin` is
`tuple[User, Admin]`, and `bearer | public` allows anonymous callers. Guards run
before the body is decoded, so an unauthenticated caller never sees field errors.

**Validation at the boundary.** Path, query, header and cookie values are converted
(`"42"` to `42`); the JSON body is decoded strictly. Failures are RFC 9457 problems
with the location of the error, for example `$.body.email`. Startup refuses to
run, and lists every problem, if the contract is inconsistent: a path placeholder
without a `Path` field, an operation the implementation does not override, a guard
nobody implemented, duplicate `operationId`s or routes.

**OpenAPI.** The document is derived from the mounted contracts, is deterministic,
and is served at `/openapi.json`. Export it for review or for a CI snapshot:

```bash
lettia openapi export examples.users_api:app --output examples/openapi.json
```

`App(docs_url="/docs")` adds an interactive viewer (Scalar, or Swagger UI with
`docs_ui="swagger"`) loaded from a pinned, integrity-checked CDN file.

**A small core.** Routes that do not belong in a contract (health checks,
webhooks, files) use the Echo-style core directly: `app.get("/x", handler)`,
groups, `Middleware = Callable[[Handler], Handler]`, and the built-in
`request_id`, `access_log` and `cors` in `lettia.middleware`. Resources that must be
released are held with `app.lifespan(context)`, an async context manager that is
closed even when a later startup step fails. Errors are turned
into responses at every layer, so an exception in a middleware still reaches the
middleware outside it as a response. `import lettia.core`
does not load the contract layer.

**Testing.** Handlers are ordinary async methods, so call them directly with
`make_context(principal=...)`; run the whole stack, lifespan included, with
`TestClient`:

```python
from lettia.testing import TestClient, make_context

res = await UserService(users).get_user(make_context(principal=Caller("ann")), req)

async with TestClient(app) as client:
    assert (await client.get("/v1/users/1", headers=auth)).status_code == 200
```

## Type checking

pyrefly is the primary checker; pyright and mypy are checked too. Projects using
lettia should enable strict checking, because pyrefly's default preset is lenient:

```toml
[tool.pyrefly]
preset = "strict"
```

The guarantees listed above are verified by the fixtures in `tests/typing/`, which
all three checkers must agree on.

## Performance

Measured on one development machine (Windows 11, Python 3.12), one uvicorn worker,
real HTTP connections, `python -m benchmarks.run_server_benchmark`:

| Scenario | Result |
| --- | --- |
| S1 static route, plain text | 1.05x Starlette |
| S2 path parameter, JSON | 1.09x Starlette |
| S3 guard, body decoding, JSON | 3% slower than an equivalent hand-written core handler |

The server dominates the cost per request, which compresses differences between
frameworks; the in-process benchmark (`python -m benchmarks.run_benchmark`) isolates
the framework itself and shows about 1.7x Starlette on S1 and S2. Treat the numbers
as ratios for one machine, not as guarantees.

## Status and scope

Version 0.1, under development. Not in 0.1: code generation or importing existing
specs, OpenAPI 3.0, pydantic, trio/anyio and sync handlers, a DI container (the
`lettia-di` extension is designed separately), content negotiation by `Accept`,
typed server-sent events, and WebSockets. See [`spec/_Design.md`](spec/_Design.md)
for the design and the reasons.

## Development

```bash
uv sync --all-extras
uv run pytest                              # unit, integration, typing, architecture
uv run pytest -m "not server"              # without starting uvicorn/hypercorn/granian
uv run ruff check src tests benchmarks examples
uv run pyrefly check && uv run pyright && uv run mypy
```

See [`AGENTS.md`](AGENTS.md) for the repository layout and conventions.

## License

MIT
