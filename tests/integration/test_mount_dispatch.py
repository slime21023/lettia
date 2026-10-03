import pytest
from hypothesis import given

from lettia import App, Context, WebSocketContext
from lettia.asgi import (
    HTTPSendEvent,
    WebSocketAcceptEvent,
    WebSocketCloseEvent,
    WebSocketSendEvent,
)
from lettia.middleware import Handler
from lettia.response import Response
from tests.support.asgi import (
    http_receive,
    http_scope,
    http_sender,
    response_body,
    websocket_receive,
    websocket_scope,
    websocket_sender,
)
from tests.support.strategies import SEGMENTS

MOUNTS: list[tuple[str, str, str, dict[str, str]]] = [
    ("", "/health", "/health", {}),
    ("/api", "/api/health", "/health", {}),
    ("/api/", "/api/health", "/health", {}),
    ("/api/", "/api//health", "/health", {}),
    ("/", "//health", "/health", {}),
    ("/api", "/health", "/health", {}),
    ("/api", "/api", "/", {}),
    ("/api", "/api/", "/", {}),
    ("/api", "/api2/health", "/api2/health", {}),
    ("/api", "/api/api/health", "/api/health", {}),
    ("/tenant/api", "/tenant/api/files/a/b", "/files/*filepath", {"filepath": "a/b"}),
    ("/租戶", "/租戶/users/甲", "/users/:name", {"name": "甲"}),
]


@pytest.mark.contract("ROUTE-MOUNT")
@pytest.mark.parametrize(("root", "path", "pattern", "params"), MOUNTS)
@pytest.mark.parametrize("method", ["GET", "HEAD", "POST", "OPTIONS"])
async def test_http_mount_paths_share_routing_and_method_detection(
    root: str,
    path: str,
    pattern: str,
    params: dict[str, str],
    method: str,
) -> None:
    app = App()
    seen: list[str] = []

    @app.get(pattern)
    def route(ctx: Context) -> str:
        seen.append(ctx.path)
        assert ctx.path_params == params
        assert ctx.scope["root_path"] == root
        return "ok"

    scope = http_scope(method=method, path=path)
    scope["root_path"] = root
    sent: list[HTTPSendEvent] = []
    await app(scope, http_receive([]), http_sender(sent))

    start = sent[0]
    assert start["type"] == "http.response.start"
    if method in ("GET", "HEAD"):
        assert start["status"] == 200 and seen == [path]
        assert response_body(sent) == (b"ok" if method == "GET" else b"")
    else:
        assert start["status"] == 405 and not seen
        assert dict(start["headers"])[b"allow"] == b"GET, HEAD"


@pytest.mark.contract("ROUTE-MOUNT")
@pytest.mark.parametrize(("root", "path", "pattern", "params"), MOUNTS)
async def test_websocket_mount_paths_preserve_external_context(
    root: str,
    path: str,
    pattern: str,
    params: dict[str, str],
) -> None:
    app = App()

    @app.websocket(pattern)
    async def route(ctx: WebSocketContext) -> None:
        assert ctx.path == path and ctx.path_params == params
        await ctx.accept()
        await ctx.send_text("ok")
        await ctx.close()

    scope = websocket_scope(path=path)
    scope["root_path"] = root
    sent: list[WebSocketAcceptEvent | WebSocketCloseEvent | WebSocketSendEvent] = []
    await app(
        scope,
        websocket_receive([{"type": "websocket.connect"}]),
        websocket_sender(sent),
    )
    assert [message["type"] for message in sent] == [
        "websocket.accept",
        "websocket.send",
        "websocket.close",
    ]


@pytest.mark.contract("ROUTE-MOUNT")
@given(root=SEGMENTS, suffix=SEGMENTS)
async def test_mount_prefix_never_strips_partial_segments(
    root: str, suffix: str
) -> None:
    app = App()
    path = "/" + root + "-" + suffix
    app.get(path)(lambda ctx: "ok")
    scope = http_scope(path=path)
    scope["root_path"] = "/" + root
    sent: list[HTTPSendEvent] = []
    await app(scope, http_receive([]), http_sender(sent))
    assert response_body(sent) == b"ok"


@pytest.mark.contract("ROUTE-MOUNT")
@pytest.mark.parametrize("rewritten", ["/new", "/api/new"])
async def test_mounted_pre_middleware_rewrites_are_resolved_at_dispatch(
    rewritten: str,
) -> None:
    app = App()

    def rewrite(next_handler: Handler) -> Handler:
        async def handler(ctx: Context) -> Response:
            assert ctx.path == "/api/old"
            ctx.path = rewritten
            return await next_handler(ctx)

        return handler

    app.use_pre(rewrite)
    app.get("/new")(lambda ctx: ctx.path)
    scope = http_scope(path="/api/old")
    scope["root_path"] = "/api"
    sent: list[HTTPSendEvent] = []
    await app(scope, http_receive([]), http_sender(sent))
    assert response_body(sent).decode() == rewritten


@pytest.mark.contract("ROUTE-MOUNT")
async def test_mounted_missing_route_remains_not_found() -> None:
    app = App()
    app.get("/health")(lambda ctx: "ok")
    scope = http_scope(path="/api/missing")
    scope["root_path"] = "/api"
    sent: list[HTTPSendEvent] = []
    await app(scope, http_receive([]), http_sender(sent))
    assert response_body(sent) == b"Not Found"
    start = sent[0]
    assert start["type"] == "http.response.start" and start["status"] == 404
