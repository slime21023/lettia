import logging
from collections.abc import AsyncIterator

import httpx
import pytest

from lettia.core import (
    App,
    ClientDisconnect,
    Context,
    Forbidden,
    Handler,
    Middleware,
    Request,
    Response,
)
from lettia.middleware import recover


def client(app: App) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def raises(error: Exception) -> Middleware:
    def middleware(next_: Handler) -> Handler:
        async def run(c: Context) -> Response:
            raise error

        return run

    return middleware


async def ok(c: Context) -> Response:
    return c.text(200, "ok")


def observer(seen: list[str]) -> Middleware:
    def middleware(next_: Handler) -> Handler:
        async def run(c: Context) -> Response:
            try:
                response = await next_(c)
            except Exception as exc:
                seen.append(f"exception:{type(exc).__name__}")
                raise
            seen.append(f"response:{response.status}")
            return response

        return run

    return middleware


async def test_recover_turns_an_inner_middleware_failure_into_a_500_problem(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = App()
    app.use(recover(), raises(RuntimeError("inner bug")))
    app.get("/x", ok)

    with caplog.at_level(logging.ERROR, logger="lettia"):
        async with client(app) as http:
            res = await http.get("/x")

    assert res.status_code == 500
    assert res.headers["content-type"] == "application/problem+json"
    assert res.json() == {
        "type": "about:blank",
        "title": "Internal Server Error",
        "status": 500,
    }
    assert any("inner bug" in str(r.exc_info) for r in caplog.records)


async def test_recover_lets_outer_middleware_see_a_response_not_an_exception() -> None:
    seen: list[str] = []
    app = App()
    app.use(observer(seen), recover(), raises(RuntimeError("inner bug")))
    app.get("/x", ok)

    async with client(app) as http:
        await http.get("/x")

    assert seen == ["response:500"]


async def test_without_recover_outer_middleware_sees_the_exception() -> None:
    seen: list[str] = []
    app = App()
    app.use(observer(seen), raises(RuntimeError("inner bug")))
    app.get("/x", ok)

    async with client(app) as http:
        res = await http.get("/x")

    assert seen == ["exception:RuntimeError"]
    assert res.status_code == 500  # the app's own safety net still answers


async def test_recover_debug_mode_adds_an_exception_summary() -> None:
    app = App()
    app.use(recover(debug=True), raises(ValueError("boom")))
    app.get("/x", ok)

    async with client(app) as http:
        res = await http.get("/x")

    assert res.json()["detail"] == "ValueError: boom"


async def test_recover_keeps_the_status_of_an_http_error_from_inner_middleware(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = App()
    app.use(recover(), raises(Forbidden("no")))
    app.get("/x", ok)

    with caplog.at_level(logging.ERROR, logger="lettia"):
        async with client(app) as http:
            res = await http.get("/x")

    assert res.status_code == 403
    assert res.json()["detail"] == "no"
    assert caplog.records == []


async def test_recover_passes_normal_responses_through_untouched() -> None:
    response = Response(200)

    async def handler(c: Context) -> Response:
        return response

    async def no_body() -> AsyncIterator[bytes]:
        return
        yield b""  # pragma: no cover - makes this an async generator

    c = Context(Request(method="GET", path="/", body=no_body()))

    assert await recover()(handler)(c) is response


async def test_recover_does_not_swallow_a_client_disconnect() -> None:
    async def handler(c: Context) -> Response:
        raise ClientDisconnect

    async def no_body() -> AsyncIterator[bytes]:
        return
        yield b""  # pragma: no cover - makes this an async generator

    c = Context(Request(method="GET", path="/", body=no_body()))

    with pytest.raises(ClientDisconnect):
        await recover()(handler)(c)
