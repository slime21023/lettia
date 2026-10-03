# Lettia

Lettia is a small, type-first ASGI toolkit for Python 3.12+. It provides the
core building blocks for HTTP APIs and real-time endpoints while keeping the
application model close to native ASGI:

- method-aware routing with static, parameter, wildcard, and named routes;
- lazy request parsing through `Context`;
- composable pre-routing, global, route, and group middleware;
- text, JSON, bytes, streaming, cookie, and header responses;
- explicit WebSocket and application-lifespan handling;
- typed binding and validation through small extension protocols; and
- synchronous and asynchronous testing through HTTPX ASGI transport.

Lettia is designed as an ASGI application kernel. It does not impose a
database layer, dependency-injection container, template engine, configuration
system, or background-job queue.

## Quick start

### Install

```bash
uv add lettia uvicorn
```

To test your application:

```bash
uv add --dev "lettia[testing]" pytest pytest-asyncio
```

The runtime dependency is `attrs`. Optional extras are available for Pydantic
and HTTPX-based testing:

```bash
uv add "lettia[pydantic]"
uv add "lettia[testing]"
```

### Create an application

```python
# app.py
from lettia import App, Context

app = App()


@app.get("/health")
def health(ctx: Context) -> dict[str, str]:
    return {"status": "ok"}


@app.get("/users/:user_id")
def get_user(ctx: Context) -> dict[str, str]:
    return {"id": ctx.path_params["user_id"]}
```

Run it with Uvicorn:

```bash
uv run uvicorn app:app --reload
```

Then open `http://127.0.0.1:8000/health` or
`http://127.0.0.1:8000/users/42`.

## Working with requests

Register a handler on `App`, read request data through `Context`, and return a
value. Dictionaries become JSON responses and strings become text. Use an
explicit response for status, headers, cookies or streaming. Synchronous
handlers run directly on the event-loop thread; use awaitable I/O in async
handlers. See [Build your first app](docs/getting_started.md) for typed input,
expected failures and a runnable test file.

## Routing

| Pattern | Meaning |
|---|---|
| `/health` | Exact route |
| `/users/:user_id` | Dynamic path segment |
| `/files/*filepath` | Remaining path |

Routes support groups, named URL generation, HEAD fallback, and 404/405
responses. See [Routes and groups](docs/routing.md) for registration and
matching rules.

## Context, binding, and state

`Context` provides lazy request parsing, typed model binding, and request-local
state through `StateKey[T]`. Built-in binders support attrs, dataclasses and
optional Pydantic models. JSON fields take priority over query parameters;
the basic binders support scalar types. See [Requests and binding](docs/context_and_binding.md)
for input rules and [the API](docs/api/context.md) for exact contracts.

## Responses and errors

Return a value or construct `JsonResponse`, `TextResponse` or `StreamResponse`.
Use `ctx.abort()` for expected HTTP failures and `@app.error_handler` for a
custom error format. See [Responses and errors](docs/responses.md) for status
codes, headers, cookies, streaming and a complete JSON error-handler example.

## Middleware and built-in capabilities

Add the following to the application above:

```python
from lettia.middleware import body_limit, cors, request_id, request_logger

app.use(
    request_id(),
    request_logger(),
    cors(allow_origins=["https://frontend.example"]),
    body_limit(max_bytes=1024 * 1024),
)
```

The first middleware is outermost. This order applies Request ID and logging
before CORS can answer preflight early. Global middleware covers routing errors;
route/group middleware runs only after matching. Use `app.use_pre()` for
pre-routing work.

Built-ins include CORS, Request ID, request logging, body limits, timeouts,
process-local rate limiting, signed Cookie sessions and standalone-chain
recovery. Custom middleware uses the same callable interface; no plugin
registration system is required. See [Middleware](docs/middleware.md) for
configuration, ordering and custom middleware examples.

## WebSockets and lifespan

WebSocket handlers use `WebSocketContext` and bypass HTTP middleware. App also
provides explicit startup/shutdown hooks. See [WebSockets](docs/websocket.md)
and [Deployment](docs/deployment.md#application-factory-and-lifecycle).

## Production boundary

Run Lettia with an ASGI server; the hosting environment owns TLS, proxy trust,
worker management and centralized observability. Rate limiting is process-local,
and post-response tasks are best-effort. Application-wide dependencies are
passed explicitly through an app factory. See [Deployment](docs/deployment.md)
for these boundaries and Session cookie settings.

## Extensions and static files

Small Binder, Validator and Renderer protocols support replaceable adapters.
`lettia.ext.StaticFiles` provides conditional and range requests with streaming
and traversal checks. See [Protocols](docs/api/extensions.md) and
[Static files](docs/static_files.md); integrations remain optional.

## Testing and quality checks

After installing the testing dependencies above, save this as `test_app.py`
beside `app.py`:

```python
from lettia.testing import TestClient

from app import app


def test_health() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

Run `uv run python -m pytest`. The synchronous client is intended for individual
HTTP requests; it does not persist response cookies or run lifespan. For async
tests and cookie flows, see [Testing your app](docs/testing.md).
Framework contributors use the separate
[quality checks](docs/contributing_testing.md#verification-commands).

## Documentation map

- [Getting started](docs/getting_started.md) — build and test a JSON API.
- [Routing](docs/routing.md) — routes, groups, methods and URL generation.
- [Requests and binding](docs/context_and_binding.md) — input, models and typed state.
- [Responses and errors](docs/responses.md) — output, cookies, streams and failures.
- [Middleware](docs/middleware.md) — built-in configuration and custom behavior.
- [Testing](docs/testing.md) — application HTTP tests and transport boundaries.
- [Static files](docs/static_files.md) and [WebSockets](docs/websocket.md).
- [Deployment](docs/deployment.md) — server configuration and operational boundaries.
- [API reference](docs/api_reference.md) — public signatures and contracts.
- [Architecture](docs/architecture.md) — framework ownership and lifecycle.

## Project structure

The typed core lives in `src/lettia/`, with composable `middleware/`, extension
`protocols/` and `ext/` packages. Runnable applications live in `examples/`;
`tests/` contains the framework suite. See the
[architecture](docs/architecture.md) and
[contributor testing guide](docs/contributing_testing.md) for internal work.

## Design principles

1. Keep the ASGI boundary visible.
2. Compile route and middleware composition before the hot path.
3. Parse request data lazily and cache it per request.
4. Normalize simple handler return values into one response model.
5. Keep integrations composable instead of forcing a full-stack architecture.
6. Test public behavior and edge conditions, not only implementation details.

## License

Lettia is released under the [MIT License](LICENSE).
