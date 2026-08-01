from typing import Any

from lettia.app import App
from lettia.context import Context
from lettia.middleware import Handler


def test_group_nesting_and_prefix() -> None:
    app = App()
    api = app.group("/api/v1")
    users = api.group("/users")

    @users.get("/:id", name="user_detail")
    def get_user(ctx: Context) -> str:
        return "user_42"

    assert users.prefix == "/api/v1/users"
    match_res = app.router.match("GET", "/api/v1/users/42")
    assert match_res is not None
    route, params = match_res
    assert route.name == "user_detail"
    assert params == {"id": "42"}


def test_group_middleware_inheritance() -> None:
    events: list[str] = []

    def mw_api(next_handler: Handler) -> Handler:
        async def handler(ctx: Context) -> Any:
            events.append("api_mw")
            return await next_handler(ctx)

        return handler

    def mw_users(next_handler: Handler) -> Handler:
        async def handler(ctx: Context) -> Any:
            events.append("users_mw")
            return await next_handler(ctx)

        return handler

    app = App()
    api = app.group("/api", mw_api)
    users = api.group("/users", mw_users)

    @users.get("/profile")
    def profile(ctx: Context) -> str:
        events.append("handler")
        return "profile"

    # Pre-compile chains
    app._compile_chains()

    # Route middleware list check
    mw_list = app._route_middlewares[("GET", "/api/users/profile")]
    assert len(mw_list) == 2
    assert mw_list[0] == mw_api
    assert mw_list[1] == mw_users
