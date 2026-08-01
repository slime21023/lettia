from typing import Any

import httpx
import pytest

from lettia.app import App
from lettia.context import Context


@pytest.mark.asyncio
async def test_app_http_request_response() -> None:
    app = App()

    @app.get("/")
    def index(ctx: Context) -> str:
        return "Hello Lettia"

    @app.get("/users/:id")
    def get_user(ctx: Context) -> dict[str, Any]:
        user_id = ctx.path_params.get("id")
        return {"id": user_id, "name": f"User-{user_id}"}

    @app.post("/echo")
    async def echo(ctx: Context) -> dict[str, Any]:
        data = await ctx.json()
        return {"received": data}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # GET /
        resp_index = await client.get("/")
        assert resp_index.status_code == 200
        assert resp_index.text == "Hello Lettia"

        # GET /users/42
        resp_user = await client.get("/users/42")
        assert resp_user.status_code == 200
        assert resp_user.json() == {"id": "42", "name": "User-42"}

        # POST /echo
        resp_echo = await client.post("/echo", json={"hello": "world"})
        assert resp_echo.status_code == 200
        assert resp_echo.json() == {"received": {"hello": "world"}}

        # GET 404
        resp_404 = await client.get("/nonexistent")
        assert resp_404.status_code == 404


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

    # Simulate ASGI lifespan messages
    lifespan_messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        if not lifespan_messages:
            return {"type": "lifespan.startup"}
        return {"type": "lifespan.shutdown"}

    sent_messages: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent_messages.append(message)
        if message["type"] == "lifespan.startup.complete":
            lifespan_messages.append(message)

    scope = {"type": "lifespan"}
    await app(scope, receive, send)

    assert "startup" in events
    assert "shutdown" in events
    assert sent_messages[0]["type"] == "lifespan.startup.complete"
    assert sent_messages[1]["type"] == "lifespan.shutdown.complete"
