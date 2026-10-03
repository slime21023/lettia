import pytest

from lettia import SESSION, App, Context
from lettia.middleware import (
    request_id,
    session,
)
from lettia.response import Response
from lettia.testing import TestClient


@pytest.mark.contract("RESP-COOKIES")
def test_reused_error_response_does_not_accumulate_policy_cookies() -> None:
    app = App()
    app.use(session("secret"), request_id(generator=lambda: "stable-id"))
    shared = Response(headers={"Set-Cookie": "first=1", "set-cookie": "second=2"})
    expected_headers = shared.headers.copy()

    @app.get("/")
    def route(ctx: Context) -> Response:
        ctx.state.require(SESSION)["user"] = "alice"
        shared.headers["bad name"] = "invalid"
        return shared

    @app.error_handler
    def error_handler(ctx: Context, exc: Exception) -> Response:
        del shared.headers["bad name"]
        shared.status_code = 500
        ctx.state.require(SESSION)["user"] = "bob"
        return shared

    for _ in range(2):
        result = TestClient(app).get("/")
        cookies = result.headers.get_list("set-cookie")
        assert result.status_code == 500 and len(cookies) == 3
        assert cookies[:2] == ["first=1", "second=2"]
        assert result.headers["x-request-id"] == "stable-id"
        assert shared.headers == expected_headers
