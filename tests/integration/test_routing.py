import pytest
from hypothesis import given

from lettia import App, Context, WebSocketContext
from lettia.middleware import Handler
from lettia.response import Response, TextResponse, normalize_response
from lettia.testing import TestClient
from tests.support.strategies import SEGMENTS


@pytest.mark.contract("ROUTE-MATCH")
def test_group_middleware_inheritance() -> None:
    events: list[str] = []

    def mw_api(next_handler: Handler) -> Handler:
        async def handler(ctx: Context) -> Response:
            events.append("api_mw")
            return TextResponse(
                normalize_response(await next_handler(ctx)).body.decode()
            )

        return handler

    def mw_users(next_handler: Handler) -> Handler:
        async def handler(ctx: Context) -> Response:
            events.append("users_mw")
            return TextResponse(
                normalize_response(await next_handler(ctx)).body.decode()
            )

        return handler

    app = App()
    users = app.group("/api", mw_api).group("/users", mw_users)

    def profile(ctx: Context) -> str:
        events.append("handler")
        return "profile"

    users.get("/profile")(profile)
    response = TestClient(app).get("/api/users/profile")
    assert response.text == "profile"
    assert events == ["api_mw", "users_mw", "handler"]


@pytest.mark.contract("ROUTE-MATCH")
def test_group_normalizes_paths_and_registers_all_methods() -> None:
    app = App()
    group = app.group("api/")

    def handler(ctx: Context) -> str:
        return ctx.path

    group.get("items", name="get_items")(handler)
    group.post("items")(handler)
    group.put("items")(handler)
    group.delete("items")(handler)
    group.patch("items")(handler)
    assert group.prefix == "/api"
    client = TestClient(app)
    for method in ("GET", "POST", "PUT", "DELETE", "PATCH"):
        assert client.request(method, "/api/items").status_code == 200


@pytest.mark.contract("ROUTE-MATCH")
@given(parent=SEGMENTS, child=SEGMENTS, value=SEGMENTS)
def test_nested_group_prefixes_and_parameters_compose(
    parent: str, child: str, value: str
) -> None:
    app = App()
    group = app.group(parent).group(child)
    group.add_route("GET", ":id", lambda ctx: ctx.path_params, name="detail")
    url = app.url_for("detail", id=value)
    assert url == f"/{parent}/{child}/{value}"
    assert TestClient(app).get(url).json() == {"id": value}


@pytest.mark.contract("ROUTE-MATCH")
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


@pytest.mark.contract("ROUTE-MATCH")
def test_method_mismatch_returns_405_and_allow_header() -> None:
    app = App()
    app.add_route("GET", "/items", lambda ctx: "items")
    response = TestClient(app).post("/items")
    assert response.status_code == 405
    assert response.headers["allow"] == "GET, HEAD"


@pytest.mark.contract("ROUTE-MATCH")
def test_websocket_routes_are_not_advertised_in_allow_header() -> None:
    app = App()

    async def websocket_handler(ws: WebSocketContext) -> None:
        await ws.accept()

    app.websocket("/ws")(websocket_handler)

    response = TestClient(app).get("/ws")

    assert response.status_code == 404
    assert "allow" not in response.headers
