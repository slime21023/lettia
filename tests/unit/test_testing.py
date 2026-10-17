import subprocess
import sys
from typing import Protocol, override

import msgspec
import pytest

from lettia import App, Context, Ok, Path, op, public
from lettia.core import StartupError
from lettia.testing import TestClient, make_context


class User(msgspec.Struct):
    uid: int


class GetReq(msgspec.Struct):
    item_id: Path[int]


class ItemApi(Protocol):
    @op.get("/items/{item_id}", guard=public)
    async def get_item(self, c: Context[None], req: GetReq) -> Ok[int]: ...


class ItemService(ItemApi):
    @override
    async def get_item(self, c: Context[None], req: GetReq) -> Ok[int]:
        return Ok(req.item_id * 2)


# --- make_context ---------------------------------------------------------


def test_make_context_defaults_to_an_anonymous_get_of_the_root() -> None:
    c = make_context()

    assert c.principal is None
    assert (c.request.method, c.request.path) == ("GET", "/")
    assert c.path_params == {}


def test_make_context_carries_the_principal() -> None:
    user = User(7)

    c = make_context(principal=user)

    assert c.principal is user


def test_make_context_builds_the_request_from_its_options() -> None:
    c = make_context(
        method="POST",
        path="/items/3",
        headers={"X-Token": "abc"},
        query="a=1&a=2",
        path_params={"item_id": "3"},
    )

    assert c.request.method == "POST"
    assert c.request.path == "/items/3"
    assert c.request.headers["x-token"] == "abc"
    assert c.request.query.get_all("a") == ["1", "2"]
    assert c.path_params == {"item_id": "3"}


async def test_make_context_serves_its_body_to_the_handler() -> None:
    c = make_context(method="POST", body=b'{"x": 1}')

    assert await c.request.body() == b'{"x": 1}'


async def test_a_handler_can_be_called_directly_with_a_made_context() -> None:
    res = await ItemService().get_item(make_context(), GetReq(item_id=4))

    assert isinstance(res, Ok)
    assert res.body == 8


def test_make_context_needs_no_http_client_library() -> None:
    code = (
        "import sys; sys.modules['httpx'] = None; "
        "from lettia.testing import make_context; make_context()"
    )

    done = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )

    assert done.returncode == 0, done.stderr


# --- TestClient -----------------------------------------------------------


def build_app(log: list[str] | None = None) -> App:
    app = App()
    app.mount(ItemService())
    if log is not None:

        async def start() -> None:
            log.append("startup")

        async def stop() -> None:
            log.append("shutdown")

        app.on_startup(start)
        app.on_shutdown(stop)
    return app


async def test_test_client_sends_requests_to_the_app() -> None:
    async with TestClient(build_app()) as client:
        res = await client.get("/items/21")

    assert (res.status_code, res.json()) == (200, 42)


async def test_test_client_runs_the_lifespan_around_the_block() -> None:
    log: list[str] = []

    async with TestClient(build_app(log)) as client:
        log.append("request")
        await client.get("/items/1")

    assert log == ["startup", "request", "shutdown"]


async def test_test_client_runs_shutdown_even_when_the_block_fails() -> None:
    log: list[str] = []

    with pytest.raises(RuntimeError, match="boom"):
        async with TestClient(build_app(log)):
            raise RuntimeError("boom")

    assert log == ["startup", "shutdown"]


async def test_test_client_raises_the_startup_problems_on_entry() -> None:
    app = App()
    app.get("no-slash", None)  # type: ignore[arg-type]

    with pytest.raises(StartupError) as caught:
        async with TestClient(app):
            pass

    assert any("no-slash" in p for p in caught.value.problems)


async def test_test_client_reports_a_failing_startup_hook() -> None:
    app = build_app()

    async def broken() -> None:
        raise RuntimeError("db down")

    app.on_startup(broken)

    with pytest.raises(RuntimeError, match="db down"):
        async with TestClient(app):
            pass


async def test_test_client_is_an_httpx_client_with_a_default_base_url() -> None:
    async with TestClient(build_app()) as client:
        assert str(client.base_url) == "http://testserver"
        res = await client.get("/openapi.json")

    assert res.json()["openapi"] == "3.1.0"


def test_test_client_is_not_collected_as_a_test_class() -> None:
    assert TestClient.__test__ is False


async def test_test_client_holds_lifespan_contexts_open_for_the_block() -> None:
    from collections.abc import AsyncGenerator
    from contextlib import asynccontextmanager

    log: list[str] = []

    @asynccontextmanager
    async def pool() -> AsyncGenerator[None]:
        log.append("open")
        yield
        log.append("close")

    app = build_app()
    app.lifespan(pool)

    async with TestClient(app) as client:
        log.append("request")
        await client.get("/items/1")

    assert log == ["open", "request", "close"]
