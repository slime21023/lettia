"""Request URL information end to end, with and without ``allowed_hosts``."""

from typing import Protocol, override

import pytest

from lettia import App, Context, GuardSpec, Ok, WebSocket, op, public
from lettia.core import Context as CoreContext
from lettia.core import RequestHead, Response, Unauthorized
from lettia.testing import TestClient


async def show(c: CoreContext) -> Response:
    request = c.request
    return c.json(
        200,
        {"scheme": request.scheme, "host": request.host, "url": request.url},
    )


def make_app(**options: object) -> App:
    app = App(**options)  # type: ignore[arg-type]
    app.get("/show/{name}", show)
    return app


async def test_request_url_reports_scheme_host_and_the_raw_url() -> None:
    async with TestClient(make_app(), base_url="https://api.example.com") as http:
        res = await http.get("/show/a%2Fb?x=1&y=%20")

    assert res.json() == {
        "scheme": "https",
        "host": "api.example.com",
        "url": "https://api.example.com/show/a%2Fb?x=1&y=%20",
    }


async def test_request_url_ignores_forwarded_headers() -> None:
    headers = {
        "x-forwarded-proto": "https",
        "x-forwarded-host": "evil.example",
        "forwarded": "host=evil.example;proto=https",
    }
    async with TestClient(make_app(), base_url="http://api.example.com") as http:
        res = await http.get("/show/a", headers=headers)

    assert res.json()["scheme"] == "http"
    assert res.json()["host"] == "api.example.com"


@pytest.mark.parametrize("host", ["api.example.com", "web.example.com"])
async def test_allowed_hosts_lets_listed_hosts_through(host: str) -> None:
    app = make_app(allowed_hosts=["api.example.com", "*.example.com"])
    async with TestClient(app, base_url=f"http://{host}") as http:
        res = await http.get("/show/a")

    assert res.status_code == 200
    assert res.json()["host"] == host


async def test_allowed_hosts_answers_400_for_any_other_host() -> None:
    app = make_app(allowed_hosts=["api.example.com"])
    async with TestClient(app, base_url="http://evil.example") as http:
        res = await http.get("/show/a")

    assert res.status_code == 400
    assert res.headers["content-type"] == "application/problem+json"


async def test_allowed_hosts_only_guard_what_reads_the_host() -> None:
    async def plain(c: CoreContext) -> Response:
        return c.text(200, "ok")

    app = App(allowed_hosts=["api.example.com"])
    app.get("/plain", plain)
    async with TestClient(app, base_url="http://evil.example") as http:
        res = await http.get("/plain")

    assert res.status_code == 200


def test_allowed_hosts_with_an_invalid_pattern_fails_at_construction() -> None:
    with pytest.raises(ValueError, match="allowed host pattern"):
        App(allowed_hosts=["api.example.com:8080"])


seen: list[tuple[str, str]] = []
viewer = GuardSpec[str]("viewer")


async def verify(head: RequestHead) -> str:
    seen.append((head.scheme, head.host))
    if head.host != "testserver":
        raise Unauthorized("wrong host")
    return "v"


class Api(Protocol):
    @op.websocket("/live", guard=viewer)
    async def live(self, c: Context[str], ws: WebSocket) -> None: ...

    @op.get("/ping", guard=public)
    async def ping(self, c: Context[None], req: None) -> Ok[str]: ...


class Service(Api):
    @override
    async def live(self, c: Context[str], ws: WebSocket) -> None:
        await ws.accept()
        await ws.send_text("hi")
        await ws.close()

    @override
    async def ping(self, c: Context[None], req: None) -> Ok[str]:
        return Ok("pong")


async def test_a_websocket_guard_sees_the_scheme_and_host_of_the_handshake() -> None:
    seen.clear()
    app = App(guards=[viewer.use(verify)])
    app.mount(Service())
    async with TestClient(app) as http:
        async with http.websocket_connect("/live") as ws:
            assert await ws.receive_text() == "hi"

    assert seen == [("ws", "testserver")]
