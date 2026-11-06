# Testing

`lettia.testing` has two tools. `make_context` builds a request context
without ASGI so a handler method can be called like any async function.
`TestClient` is an `httpx.AsyncClient` bound to an app in memory, with startup
and shutdown around it. Both work from plain `pytest`.

## Install

`make_context` needs nothing beyond lettia. `TestClient` needs `httpx` and is
imported lazily on first use:

```
pip install 'lettia[test]'
```

This repo's own tests run with `pytest-asyncio` and, in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
addopts = ["--strict-config", "--strict-markers"]
```

With `asyncio_mode = "auto"` every `async def test_...` is run on an event loop
with no decorator. Without it, mark tests with `@pytest.mark.asyncio`.

## `make_context`

```python
make_context(principal=None, *, method="GET", path="/", headers=None,
             query="", body=b"", path_params=None) -> Context[G]
```

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `principal` | `G` | `None` | Value for `c.principal`, as a guard would have produced. Positional. |
| `method` | `str` | `"GET"` | Request method. |
| `path` | `str` | `"/"` | Request path. |
| `headers` | `Mapping[str, str] \| None` | `None` | Header names are lower-cased; values are latin-1 encoded. |
| `query` | `str` | `""` | Raw query string, without `?`. |
| `body` | `bytes` | `b""` | Served from memory; `await c.request.body()` returns it. |
| `path_params` | `Mapping[str, str] \| None` | `None` | Copied into `c.path_params`. |

The request has no body-size limit (`max_body_size=None`) and no ASGI scope is
involved. Nothing is mounted: no guard runs, no middleware, no decoding. The
caller supplies the already-decoded request struct.

```python
from typing import override

import msgspec
from lettia import Context, Ok, Path, op, public
from lettia.testing import make_context


class User(msgspec.Struct):
    uid: int


class GetReq(msgspec.Struct):
    item_id: Path[int]


class ItemApi:
    @op.get("/items/{item_id}", guard=public)
    async def get_item(self, c: Context[None], req: GetReq) -> Ok[int]: ...


class ItemService(ItemApi):
    @override
    async def get_item(self, c: Context[None], req: GetReq) -> Ok[int]:
        return Ok(req.item_id * 2)


async def test_get_item() -> None:
    res = await ItemService().get_item(make_context(), GetReq(item_id=4))
    assert res.body == 8


def test_context_parts() -> None:
    c = make_context(
        User(7), method="POST", path="/items/3", headers={"X-A": "1"},
        query="a=1", body=b"{}", path_params={"item_id": "3"},
    )
    assert c.principal == User(7)
    assert c.request.headers.get("x-a") == "1"
    assert c.path_params == {"item_id": "3"}
```

The return value of a contract method is a reply object (`Ok`, ...): assert on
its `.body` (and, for other reply types, their own fields) rather than on an
HTTP response. To test encoding, status codes or decoding, go through
`TestClient`.

## `TestClient`

```python
TestClient(app, *, base_url="http://testserver", **options)
```

`app` is a `lettia.App` or a `lettia.core.App`. Extra `options` go to
`httpx.AsyncClient`. The class sets `__test__ = False` so pytest does not try
to collect it.

Use it as an async context manager:

- On entry it calls `app.build()`, so startup checks run and a failure raises
  `lettia.core.errors.StartupError` (with `.problems`) right there. It then
  drives the ASGI lifespan: `app.lifespan(...)` contexts are entered in order,
  then startup hooks run. A failing startup hook raises out of the `async with`.
- On exit, shutdown hooks run and lifespan contexts are closed, even if the
  block raised.

Requests are the `httpx.AsyncClient` methods (`get`, `post`, `put`, `patch`,
`delete`, `request`, ...) with the usual keyword arguments (`headers`,
`params`, `json`, `data`, `content`, `files`, ...), and return an
`httpx.Response` (`status_code`, `headers`, `text`, `json()`).

```python
import msgspec
from lettia import App, Context, GuardSpec, RequestHead, op, public, Ok
from lettia.core import Forbidden
from lettia.testing import TestClient


class Admin(msgspec.Struct):
    uid: int


admin = GuardSpec[Admin]("admin")


async def require_admin(head: RequestHead) -> Admin:
    if head.headers.get("x-admin") != "1":
        raise Forbidden("admins only")
    return Admin(1)


class SecretApi:
    @op.get("/secret", guard=admin)
    async def secret(self, c: Context[Admin], req: None) -> Ok[str]: ...


class SecretService(SecretApi):
    async def secret(self, c: Context[Admin], req: None) -> Ok[str]:
        return Ok(f"hello {c.principal.uid}")


def build() -> App:
    app = App(guards=[admin.use(require_admin)])
    app.mount(SecretService())
    return app


async def test_secret() -> None:
    async with TestClient(build()) as client:
        refused = await client.get("/secret")
        allowed = await client.get("/secret", headers={"x-admin": "1"})

    assert refused.status_code == 403
    assert refused.headers["content-type"] == "application/problem+json"
    assert refused.json()["detail"] == "admins only"
    assert (allowed.status_code, allowed.json()) == (200, "hello 1")
```

A core-only app works the same way:

```python
from lettia.core import App as CoreApp, Context as CoreContext, Response
from lettia.testing import TestClient


async def ping(c: CoreContext) -> Response:
    return c.text(200, "pong")


async def test_ping() -> None:
    core = CoreApp()
    core.get("/ping", ping)
    async with TestClient(core) as client:
        assert (await client.get("/ping")).text == "pong"
```

A `lettia.App` also serves its OpenAPI document at `/openapi.json` by default,
so `(await client.get("/openapi.json")).json()` is available in tests.

WebSocket testing is in [websocket.md](websocket.md): use
`client.websocket_connect(path, headers=..., subprotocols=...)`.

## Testing guards

A guard implementation is a plain `async def (head: RequestHead) -> G`, so it
can be called directly. Build a `RequestHead` with keyword arguments `method`,
`path`, and optionally `raw_path`, `query_string` (bytes), `headers` (a list of
raw `(bytes, bytes)` pairs) and `client`:

```python
import pytest
from lettia import RequestHead
from lettia.core import Unauthorized


async def test_verify() -> None:
    ok = RequestHead(method="GET", path="/", headers=[(b"authorization", b"Bearer ok")])
    assert (await verify(ok)).name == "ann"
    with pytest.raises(Unauthorized):
        await verify(RequestHead(method="GET", path="/"))
```

(`verify` is the guard function from [websocket.md](websocket.md).) To test a
guard together with its routes, mount the service on an app and call it through
`TestClient`, as in the example above: a refusal is an HTTP 401 or 403 problem
response. Combined guards (`a | b`, `a & b`) are covered the same way. See
[guards.md](guards.md).

To test a handler that depends on a guard's result without running the guard,
pass the principal to `make_context(principal)`.

## Testing middleware

A middleware is `Callable[[Handler], Handler]`. Register it on an app and
observe it through `TestClient`:

```python
from lettia import App
from lettia.core import Context as CoreContext, Handler, Response
from lettia.testing import TestClient


def tag(next_: Handler) -> Handler:
    async def run(c: CoreContext) -> Response:
        res = await next_(c)
        res.headers.set("x-tag", "1")
        return res

    return run


async def test_tag() -> None:
    app = App()
    app.use(tag)
    app.mount(ItemService())            # from the make_context example
    async with TestClient(app) as client:
        assert (await client.get("/items/21")).headers["x-tag"] == "1"
        assert (await client.get("/nope")).headers["x-tag"] == "1"   # 404 is wrapped
```

Middleware wraps 404 and 405 responses from app-level `use`, but not group
middleware. It does not run for WebSocket routes.

## Testing lifespan

`TestClient` runs the whole lifespan, so resources and hooks are observable:

```python
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from lettia import App
from lettia.testing import TestClient


async def test_lifespan() -> None:
    log: list[str] = []

    @asynccontextmanager
    async def pool() -> AsyncGenerator[None]:
        log.append("open")
        yield
        log.append("close")

    async def up() -> None:
        log.append("up")

    async def down() -> None:
        log.append("down")

    app = App()
    app.lifespan(pool)
    app.on_startup(up)
    app.on_shutdown(down)

    async with TestClient(app):
        log.append("request")

    assert log == ["open", "up", "request", "down", "close"]
```

Startup problems are raised on entry:

```python
import pytest
from lettia import App
from lettia.core.errors import StartupError
from lettia.testing import TestClient


async def test_bad_route() -> None:
    app = App()
    app.get("nope", None)  # type: ignore[arg-type]
    with pytest.raises(StartupError) as caught:
        async with TestClient(app):
            pass
    assert caught.value.problems == ["invalid path 'nope': it must start with '/'"]
```

## Server compatibility tests

`tests/integration/test_servers.py` starts `examples.users_api:app` under real
ASGI servers (uvicorn, hypercorn, granian) in subprocesses and checks the same
behavior over HTTP. A server that is not installed is skipped. The module is
marked `server`, a marker registered in `pyproject.toml`; skip these tests with:

```
pytest -m "not server"
```

## See also

- [getting-started.md](getting-started.md) — first app and test
- [contract.md](contract.md) — operations and mounting
- [guards.md](guards.md) — guards
- [websocket.md](websocket.md) — WebSocket sessions in tests
- [extensions.md](extensions.md) — di, tasks, staticfiles, docs, sse, sessions, ratelimit
