import pytest
from asgi_helpers import (
    LifespanSendEvent,
    lifespan_receive,
    lifespan_scope,
    lifespan_sender,
)

from lettia import App, Context, JSONValue
from lettia.testing import TestClient


def test_app_has_no_application_state() -> None:
    app = App()

    assert not hasattr(app, "state")
    with pytest.raises(AttributeError):
        object.__setattr__(app, "state", {})


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


def test_app_handles_default_and_custom_errors() -> None:
    app = App()

    def explode(ctx: Context) -> str:
        raise RuntimeError("boom")

    app.add_route("GET", "/explode", explode)
    assert TestClient(app).get("/explode").status_code == 500

    custom_app = App()

    async def handle_error(ctx: Context, exc: Exception) -> str:
        return f"handled: {exc}"

    custom_app.set_error_handler(handle_error)
    custom_app.add_route("GET", "/explode", explode)
    response = TestClient(custom_app).get("/explode")
    assert response.status_code == 200
    assert response.text == "handled: boom"


@pytest.mark.asyncio
async def test_app_lifespan() -> None:
    app = App()
    events: list[str] = []

    def startup() -> None:
        events.append("startup")

    def shutdown() -> None:
        events.append("shutdown")

    app.on_startup.append(startup)
    app.on_shutdown.append(shutdown)

    sent_messages: list[LifespanSendEvent] = []
    await app(
        lifespan_scope(),
        lifespan_receive([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}]),
        lifespan_sender(sent_messages),
    )

    assert events == ["startup", "shutdown"]
    assert sent_messages[0]["type"] == "lifespan.startup.complete"
    assert sent_messages[1]["type"] == "lifespan.shutdown.complete"


@pytest.mark.asyncio
async def test_app_lifespan_reports_startup_failure() -> None:
    app = App()

    def fail_startup() -> None:
        raise RuntimeError("startup failed")

    app.on_startup.append(fail_startup)
    sent_messages: list[LifespanSendEvent] = []
    await app(
        lifespan_scope(),
        lifespan_receive([{"type": "lifespan.startup"}]),
        lifespan_sender(sent_messages),
    )
    assert sent_messages == [
        {"type": "lifespan.startup.failed", "message": "startup failed"}
    ]
