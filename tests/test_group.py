from lettia import App, Context
from lettia.middleware import Handler
from lettia.response import Response, TextResponse, normalize_response
from lettia.testing import TestClient


def test_group_nesting_and_prefix() -> None:
    app = App()
    users = app.group("/api/v1").group("/users")

    def get_user(ctx: Context) -> str:
        return "user_42"

    users.get("/:id", name="user_detail")(get_user)
    assert users.prefix == "/api/v1/users"
    response = TestClient(app).get("/api/v1/users/42")
    assert response.status_code == 200
    assert response.text == "user_42"


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
