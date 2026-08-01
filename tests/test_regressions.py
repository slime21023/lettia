from pathlib import Path
from typing import Any

import httpx
import pytest

from lettia import App, WebSocketContext
from lettia.context import Context
from lettia.errors import HTTPException
from lettia.ext import StaticFiles
from lettia.middleware import cors, recover
from lettia.router import Router


@pytest.mark.asyncio
async def test_pre_middleware_can_normalize_path_before_routing() -> None:
    app = App()

    def normalize_path(next_handler: Any) -> Any:
        async def handler(ctx: Context) -> Any:
            if ctx.path != "/" and ctx.path.endswith("/"):
                ctx.path = ctx.path.rstrip("/")
            return await next_handler(ctx)

        return handler

    app.use_pre(normalize_path)

    @app.get("/health")
    def health(ctx: Context) -> str:
        return "ok"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/health/")

    assert response.status_code == 200
    assert response.text == "ok"


@pytest.mark.asyncio
async def test_global_cors_handles_preflight_without_options_route() -> None:
    app = App()
    app.use(cors(allow_origins=["https://example.com"]))

    @app.get("/resource")
    def resource(ctx: Context) -> str:
        return "ok"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.options(
            "/resource",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == "https://example.com"


@pytest.mark.asyncio
async def test_recover_preserves_http_exception_status() -> None:
    app = App()
    app.use(recover())

    @app.get("/missing")
    def missing(ctx: Context) -> str:
        ctx.abort(404, "missing")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/missing")

    assert response.status_code == 404
    assert response.text == "missing"


@pytest.mark.asyncio
async def test_method_mismatch_returns_405_and_allow_header() -> None:
    app = App()

    @app.get("/items")
    def items(ctx: Context) -> str:
        return "items"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/items")

    assert response.status_code == 405
    assert response.headers["allow"] == "GET, HEAD"


@pytest.mark.asyncio
async def test_head_falls_back_to_get_without_sending_body() -> None:
    app = App()

    @app.get("/hello")
    def hello(ctx: Context) -> str:
        return "hello"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.head("/hello")

    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-length"] == "5"


@pytest.mark.asyncio
async def test_body_disconnect_raises_http_error_instead_of_looping() -> None:
    async def receive() -> dict[str, Any]:
        return {"type": "http.disconnect"}

    ctx = Context(scope={"type": "http"}, receive=receive, send=None)

    with pytest.raises(HTTPException, match="disconnected") as exc_info:
        await ctx.body()

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_invalid_json_is_reported_as_bad_request() -> None:
    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"{bad", "more_body": False}

    ctx = Context(scope={"type": "http"}, receive=receive, send=None)

    with pytest.raises(HTTPException, match="Invalid JSON") as exc_info:
        await ctx.json()

    assert exc_info.value.status_code == 400


def test_router_keeps_parameter_names_per_method() -> None:
    router = Router()
    router.add_route("GET", "/users/:id", "get-user")
    router.add_route("POST", "/users/:user_id", "create-user")

    result = router.match("POST", "/users/7")

    assert result is not None
    assert result[0].handler == "create-user"
    assert result[1] == {"user_id": "7"}


def test_url_for_validates_and_encodes_parameters() -> None:
    router = Router()
    router.add_route("GET", "/users/:id", "user", name="user")
    router.add_route("GET", "/files/*path", "file", name="file")

    assert router.url_for("user", id="a b") == "/users/a%20b"
    assert router.url_for("file", path="docs/read me.txt") == (
        "/files/docs/read%20me.txt"
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

    static = StaticFiles(str(root))
    ctx = Context(
        scope={"type": "http", "method": "GET", "path": "/static/secret.txt"},
        receive=None,
        send=None,
        path_params={"filepath": "../www-evil/secret.txt"},
    )

    with pytest.raises(HTTPException, match="Forbidden") as exc_info:
        await static.handle(ctx)

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_static_files_supports_suffix_ranges(tmp_path: Path) -> None:
    file_path = tmp_path / "hello.txt"
    file_path.write_text("Hello Static File", encoding="utf-8")
    static = StaticFiles(str(tmp_path))
    ctx = Context(
        scope={
            "type": "http",
            "method": "GET",
            "path": "/hello.txt",
            "headers": [(b"range", b"bytes=-4")],
        },
        receive=None,
        send=None,
    )

    response = await static.handle(ctx)

    assert response.status_code == 206
    assert response.headers["content-range"] == "bytes 13-16/17"
    assert response.async_body is not None
    chunks = [chunk async for chunk in response.async_body]
    assert b"".join(chunks) == b"File"


@pytest.mark.asyncio
async def test_websocket_disconnect_is_not_reported_as_internal_error() -> None:
    app = App()

    @app.websocket("/ws")
    async def websocket_handler(ws: WebSocketContext) -> None:
        await ws.accept()
        await ws.receive_text()

    sent_messages: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent_messages.append(message)

    async def receive() -> dict[str, Any]:
        return {"type": "websocket.disconnect"}

    await app({"type": "websocket", "path": "/ws"}, receive, send)

    assert sent_messages == [{"type": "websocket.accept"}]
