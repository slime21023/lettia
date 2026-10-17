import re

import httpx
import pytest

from lettia.core import App, Context, Handler, HTTPError, Middleware, Response
from lettia.middleware import RequestId, request_id


def client(app: App) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def build(middleware: Middleware) -> App:
    async def show(c: Context) -> Response:
        return c.text(200, RequestId.get(c))

    app = App()
    app.use(middleware)
    app.get("/x", show)
    return app


async def test_request_id_generates_sixteen_hex_characters_by_default() -> None:
    async with client(build(request_id())) as http:
        res = await http.get("/x")

    assert re.fullmatch(r"[0-9a-f]{16}", res.headers["x-request-id"])


async def test_request_id_is_available_to_the_handler_and_echoed_in_the_header() -> (
    None
):
    async with client(build(request_id())) as http:
        res = await http.get("/x")

    assert res.text == res.headers["x-request-id"]


async def test_request_id_differs_between_requests() -> None:
    async with client(build(request_id())) as http:
        first = (await http.get("/x")).headers["x-request-id"]
        second = (await http.get("/x")).headers["x-request-id"]

    assert first != second


async def test_request_id_reuses_a_valid_incoming_value() -> None:
    async with client(build(request_id())) as http:
        res = await http.get("/x", headers={"x-request-id": "trace-1_A.b"})

    assert res.headers["x-request-id"] == "trace-1_A.b"
    assert res.text == "trace-1_A.b"


async def test_request_id_accepts_the_maximum_length() -> None:
    async with client(build(request_id())) as http:
        res = await http.get("/x", headers={"x-request-id": "a" * 128})

    assert res.headers["x-request-id"] == "a" * 128


@pytest.mark.parametrize("incoming", ["", "has space", "a/b", "a;b", "a" * 129])
async def test_request_id_replaces_an_untrustworthy_incoming_value(
    incoming: str,
) -> None:
    async with client(build(request_id())) as http:
        res = await http.get("/x", headers={"x-request-id": incoming})

    assert re.fullmatch(r"[0-9a-f]{16}", res.headers["x-request-id"])


async def test_request_id_uses_the_configured_header_name() -> None:
    app = build(request_id(header="X-Correlation-Id"))

    async with client(app) as http:
        res = await http.get("/x", headers={"x-correlation-id": "abc"})

    assert res.headers["x-correlation-id"] == "abc"
    assert "x-request-id" not in res.headers


async def test_request_id_uses_the_configured_generator() -> None:
    app = build(request_id(generate=lambda: "fixed"))

    async with client(app) as http:
        res = await http.get("/x")

    assert res.headers["x-request-id"] == "fixed"


async def test_request_id_is_present_on_error_and_not_found_responses() -> None:
    async def fail(c: Context) -> Response:
        raise HTTPError(403, "Forbidden")

    app = App()
    app.use(request_id())
    app.get("/fail", fail)

    async with client(app) as http:
        denied = await http.get("/fail")
        missing = await http.get("/nothing")

    assert denied.status_code == 403
    assert "x-request-id" in denied.headers
    assert missing.status_code == 404
    assert "x-request-id" in missing.headers


async def test_request_id_header_appears_once_even_if_the_handler_sets_one() -> None:
    def sets_its_own(next_: Handler) -> Handler:
        async def run(c: Context) -> Response:
            response = await next_(c)
            response.headers.set("x-request-id", "handler's")
            return response

        return run

    app = App()
    app.use(request_id(generate=lambda: "ours"), sets_its_own)
    app.get("/x", ok)

    async with client(app) as http:
        res = await http.get("/x")

    assert res.headers.get_list("x-request-id") == ["ours"]


async def ok(c: Context) -> Response:
    return c.text(200, "ok")
