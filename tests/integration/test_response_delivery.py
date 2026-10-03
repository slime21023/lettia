import pytest

from lettia import App, Context, JSONValue
from lettia.testing import TestClient


@pytest.mark.contract("RESP-WRITE")
def test_app_http_request_response() -> None:
    app = App()

    def index(ctx: Context) -> str:
        return "Hello Lettia"

    def get_user(ctx: Context) -> dict[str, str]:
        user_id = ctx.path_params.get("id", "")
        return {"id": user_id, "name": f"User-{user_id}"}

    async def echo(ctx: Context) -> dict[str, JSONValue]:
        return {"received": await ctx.json()}

    app.add_route("GET", "/", index)
    app.add_route("GET", "/users/:id", get_user)
    app.add_route("POST", "/echo", echo)

    client = TestClient(app)
    resp_index = client.get("/")
    assert resp_index.status_code == 200
    assert resp_index.text == "Hello Lettia"

    resp_user = client.get("/users/42")
    assert resp_user.status_code == 200
    assert resp_user.json() == {"id": "42", "name": "User-42"}

    resp_echo = client.post("/echo", json={"hello": "world"})
    assert resp_echo.status_code == 200
    assert resp_echo.json() == {"received": {"hello": "world"}}

    resp_404 = client.get("/nonexistent")
    assert resp_404.status_code == 404
