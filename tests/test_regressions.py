from pathlib import Path

import pytest
from asgi_helpers import (
    http_context,
    http_receive,
    http_scope,
    http_sender,
    websocket_receive,
    websocket_scope,
    websocket_sender,
)

from lettia import App, WebSocketContext
from lettia.asgi import (
    HTTPSendEvent,
    WebSocketAcceptEvent,
    WebSocketCloseEvent,
    WebSocketSendEvent,
)
from lettia.context import Context
from lettia.errors import HTTPException
from lettia.ext import StaticFiles
from lettia.middleware import Handler, cors, recover
from lettia.response import Response
from lettia.router import Router
from lettia.testing import TestClient


def test_pre_middleware_can_normalize_path_before_routing() -> None:
    app = App()

    def normalize_path(next_handler: Handler) -> Handler:
        async def handler(ctx: Context) -> Response:
            if ctx.path != "/" and ctx.path.endswith("/"):
                ctx.path = ctx.path.rstrip("/")
            return await next_handler(ctx)

        return handler

    def health(ctx: Context) -> str:
        return "ok"

    app.use_pre(normalize_path)
    app.add_route("GET", "/health", health)
    response = TestClient(app).get("/health/")
    assert response.status_code == 200
    assert response.text == "ok"


def test_global_cors_handles_preflight_without_options_route() -> None:
    app = App()
    app.use(cors(allow_origins=["https://example.com"]))
    app.add_route("GET", "/resource", lambda ctx: "ok")
    response = TestClient(app).request(
        "OPTIONS",
        "/resource",
        headers={
            "Origin": "https://example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == "https://example.com"


def test_recover_preserves_http_exception_status() -> None:
    app = App()
    app.use(recover())

    def missing(ctx: Context) -> str:
        ctx.abort(404, "missing")

    app.add_route("GET", "/missing", missing)
    response = TestClient(app).get("/missing")
    assert response.status_code == 404
    assert response.text == "missing"


def test_method_mismatch_returns_405_and_allow_header() -> None:
    app = App()
    app.add_route("GET", "/items", lambda ctx: "items")
    response = TestClient(app).post("/items")
    assert response.status_code == 405
    assert response.headers["allow"] == "GET, HEAD"


def test_head_falls_back_to_get_without_sending_body() -> None:
    app = App()
    app.add_route("GET", "/hello", lambda ctx: "hello")
    response = TestClient(app).request("HEAD", "/hello")
    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-length"] == "5"


@pytest.mark.asyncio
async def test_body_disconnect_raises_http_error_instead_of_looping() -> None:
    sent: list[HTTPSendEvent] = []
    ctx = Context(
        scope=http_scope(),
        receive=http_receive([{"type": "http.disconnect"}]),
        send=http_sender(sent),
    )
    with pytest.raises(HTTPException, match="disconnected") as exc_info:
        await ctx.body()
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_invalid_json_is_reported_as_bad_request() -> None:
    with pytest.raises(HTTPException, match="Invalid JSON") as exc_info:
        await http_context(body=b"{bad").json()
    assert exc_info.value.status_code == 400


def test_router_keeps_parameter_names_per_method() -> None:
    router: Router[str] = Router()
    router.add_route("GET", "/users/:id", "get-user")
    router.add_route("POST", "/users/:user_id", "create-user")
    result = router.match("POST", "/users/7")
    assert result is not None
    assert result[0].handler == "create-user"
    assert result[1] == {"user_id": "7"}


def test_url_for_validates_and_encodes_parameters() -> None:
    router: Router[str] = Router()
    router.add_route("GET", "/users/:id", "user", name="user")
    router.add_route("GET", "/files/*path", "file", name="file")
    assert router.url_for("user", id="a b") == "/users/a%20b"
    assert (
        router.url_for("file", path="docs/read me.txt") == "/files/docs/read%20me.txt"
    )
    with pytest.raises(KeyError, match="Missing route parameters"):
        router.url_for("user")
    with pytest.raises(TypeError, match="Unexpected route parameters"):
        router.url_for("user", id="1", extra="value")


@pytest.mark.asyncio
async def test_static_files_rejects_sibling_directory_escape(tmp_path: Path) -> None:
    root = tmp_path / "www"
    sibling = tmp_path / "www-evil"
    root.mkdir()
    sibling.mkdir()
    (sibling / "secret.txt").write_text("secret", encoding="utf-8")
    ctx = http_context(path="/static/secret.txt")
    ctx.path_params = {"filepath": "../www-evil/secret.txt"}
    with pytest.raises(HTTPException, match="Forbidden") as exc_info:
        await StaticFiles(str(root)).handle(ctx)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_static_files_supports_suffix_ranges(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("Hello Static File", encoding="utf-8")
    ctx = http_context(path="/hello.txt", headers=[(b"range", b"bytes=-4")])
    response = await StaticFiles(str(tmp_path)).handle(ctx)
    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 13-16/17"
    assert response.async_body is not None
    assert b"".join([chunk async for chunk in response.async_body]) == b"File"


@pytest.mark.asyncio
async def test_websocket_disconnect_is_not_reported_as_internal_error() -> None:
    app = App()

    async def websocket_handler(ws: WebSocketContext) -> None:
        await ws.accept()
        await ws.receive_text()

    app.websocket("/ws")(websocket_handler)
    sent: list[WebSocketAcceptEvent | WebSocketSendEvent | WebSocketCloseEvent] = []
    await app(
        websocket_scope(path="/ws"),
        websocket_receive([{"type": "websocket.disconnect", "code": 1000}]),
        websocket_sender(sent),
    )
    assert sent == [{"type": "websocket.accept"}]
