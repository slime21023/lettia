from typing import Any

import pytest

from lettia import App, Context
from lettia.middleware import MemoryRateLimiter, rate_limit, session
from lettia.testing import TestClient


def test_rate_limit_middleware() -> None:
    app = App()
    app.use(rate_limit(requests_per_minute=2))

    @app.get("/limited")
    def limited_route(ctx: Context) -> str:
        return "Allowed"

    client = TestClient(app)

    # First request: OK
    r1 = client.get("/limited")
    assert r1.status_code == 200

    # Second request: OK
    r2 = client.get("/limited")
    assert r2.status_code == 200

    # Third request: Exceeded -> HTTP 429
    r3 = client.get("/limited")
    assert r3.status_code == 429
    assert "retry-after" in r3.headers


def test_rate_limiter_rejects_non_positive_limit() -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        MemoryRateLimiter(requests_per_minute=0)


def test_signed_session_middleware() -> None:
    app = App()
    app.use(session(secret_key="super-secret-key-12345"))

    @app.get("/set-session")
    def set_session(ctx: Context) -> str:
        ctx.state["session"]["user_id"] = "user_999"
        return "Session Set"

    @app.get("/get-session")
    def get_session(ctx: Context) -> dict[str, Any]:
        user_id = ctx.state.get("session", {}).get("user_id", "guest")
        return {"user_id": user_id}

    client = TestClient(app)

    # Set session
    r1 = client.get("/set-session")
    assert r1.status_code == 200
    cookie = r1.headers.get("set-cookie")
    assert cookie is not None
    assert "session=" in cookie

    # Get session passing the signed cookie
    r2 = client.get("/get-session", headers={"cookie": cookie})
    assert r2.status_code == 200
    assert r2.json() == {"user_id": "user_999"}
