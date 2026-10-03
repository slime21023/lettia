import base64
import json
from http.cookies import SimpleCookie
from unittest.mock import patch

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from lettia import SESSION, App, Context
from lettia.asgi import JSONValue
from lettia.middleware import (
    Handler,
    session,
)
from lettia.middleware.session import _sign
from lettia.response import JsonResponse, Response
from lettia.testing import TestClient
from tests.support.strategies import JSON_OBJECTS


@pytest.mark.contract("MW-SESSION")
@pytest.mark.parametrize("name", ["session", "__Host-session", "__Secure-session"])
@pytest.mark.parametrize("same_site", ["lax", "strict", "none"])
def test_session_logout_preserves_cookie_scope_and_security(
    name: str, same_site: str
) -> None:
    app = App()
    app.use(
        session("test-secret", cookie_name=name, https_only=True, same_site=same_site)
    )

    @app.get("/login")
    def login(ctx: Context) -> str:
        ctx.state.require(SESSION)["user"] = "alice"
        return "ok"

    @app.get("/logout")
    def logout(ctx: Context) -> str:
        ctx.state.require(SESSION).clear()
        return "ok"

    client = TestClient(app, base_url="https://example.com")
    created = SimpleCookie(client.get("/login").headers["set-cookie"])
    result = client.get("/logout", headers={"cookie": f"{name}={created[name].value}"})
    cleared = SimpleCookie(result.headers["set-cookie"])
    for attribute in ("secure", "httponly", "samesite", "path", "domain"):
        assert cleared[name][attribute] == created[name][attribute]
    assert cleared[name].value == "" and cleared[name]["max-age"] == "0"


@pytest.mark.contract("MW-SESSION")
def test_signed_session_middleware() -> None:
    app = App()
    app.use(session(secret_key="super-secret-key-12345"))

    @app.get("/set-session")
    def set_session(ctx: Context) -> str:
        ctx.state.require(SESSION)["user_id"] = "user_999"
        return "Session Set"

    @app.get("/get-session")
    def get_session(ctx: Context) -> dict[str, str]:
        user_id = ctx.state.require(SESSION).get("user_id", "guest")
        if not isinstance(user_id, str):
            user_id = "guest"
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


@pytest.mark.contract("MW-SESSION")
@given(
    data=JSON_OBJECTS, lifetime=st.integers(1, 1000), delta=st.sampled_from([-1, 0, 1])
)
# Multiple HTTP/event-loop lifecycles verify expiry, not wall-clock performance.
@settings(deadline=None)
@example(data={"user": "alice"}, lifetime=1, delta=0)
def test_session_authentication_respects_signed_expiry(
    data: dict[str, JSONValue],
    lifetime: int,
    delta: int,
) -> None:
    app = App()
    app.use(session("test-secret", max_age=lifetime))

    def issue(ctx: Context) -> str:
        ctx.state.require(SESSION)["payload"] = data
        return "ok"

    app.add_route("GET", "/issue", issue)
    app.add_route("GET", "/read", lambda ctx: ctx.state.require(SESSION))
    client = TestClient(app)
    with patch("time.time", return_value=10000.0):
        response = client.get("/issue")
    cookie = response.headers["set-cookie"].split(";", 1)[0]
    with patch("time.time", return_value=10000.0 + lifetime + delta):
        restored = client.get("/read", headers={"cookie": cookie})
    assert restored.json() == ({"payload": data} if delta < 0 else {})
    with patch("time.time", return_value=9999.0):
        assert client.get("/read", headers={"cookie": cookie}).json() == {}

    token = bytearray(base64.urlsafe_b64decode(cookie.split("=", 1)[1]))
    token[0] ^= 1
    tampered = base64.urlsafe_b64encode(token).decode()
    with patch("time.time", return_value=10000.0):
        assert (
            client.get("/read", headers={"cookie": "session=" + tampered}).json() == {}
        )


@pytest.mark.contract("MW-SESSION")
@given(data=JSON_OBJECTS)
def test_session_rejects_legacy_cookies(data: dict[str, JSONValue]) -> None:
    app = App()
    app.use(session("test-secret"))
    app.add_route("GET", "/", lambda ctx: ctx.state.require(SESSION))
    legacy = _sign(json.dumps({"payload": data}).encode(), b"test-secret")
    assert (
        TestClient(app).get("/", headers={"cookie": "session=" + legacy}).json() == {}
    )


@pytest.mark.contract("MW-SESSION")
@given(
    data=JSON_OBJECTS,
    header=st.sampled_from(
        [
            {},
            {"v": True, "iat": 10000},
            {"v": 2, "iat": 10000},
            {"v": 1},
            {"v": 1, "iat": True},
            {"v": 1, "iat": "10000"},
            {"v": 1, "iat": float("nan")},
            {"v": 1, "iat": float("inf")},
            {"v": 1, "iat": 10**400},
        ]
    ),
)
def test_session_rejects_invalid_signed_envelope(
    data: dict[str, JSONValue], header: dict[str, JSONValue]
) -> None:
    app = App()
    app.use(session("test-secret"))
    app.add_route("GET", "/", lambda ctx: ctx.state.require(SESSION))
    token = _sign(json.dumps({**header, "data": data}).encode(), b"test-secret")
    with patch("time.time", return_value=10000.0):
        response = TestClient(app).get("/", headers={"cookie": "session=" + token})
    assert response.json() == {}
    assert "set-cookie" not in response.headers


@pytest.mark.contract("MW-SESSION", "APP-ERROR")
@pytest.mark.parametrize("failure", ["writer", "middleware", "handler"])
@pytest.mark.parametrize(
    ("initial_user", "route_user", "error_user"),
    [
        (None, "alice", None),
        ("alice", "alice", None),
        ("alice", "bob", "carol"),
        (None, None, "carol"),
    ],
)
def test_session_error_response_uses_latest_state(
    failure: str,
    initial_user: str | None,
    route_user: str | None,
    error_user: str | None,
) -> None:
    app = App()

    def outer(next_handler: Handler) -> Handler:
        async def handler(ctx: Context) -> Response:
            response = await next_handler(ctx)
            if ctx.path == "/" and failure == "middleware":
                raise ValueError("outer failure")
            return response

        return handler

    app.use(outer, session("secret", https_only=True, same_site="none"))

    @app.get("/login")
    def login(ctx: Context) -> str:
        if initial_user is not None:
            ctx.state.require(SESSION)["user"] = initial_user
        return "ok"

    @app.get("/")
    def route(ctx: Context) -> Response:
        data = ctx.state.require(SESSION)
        data.clear()
        if route_user is not None:
            data["user"] = route_user
        if failure == "handler":
            raise ValueError("handler failure")
        return Response(headers={"bad name": "invalid"} if failure == "writer" else {})

    @app.get("/whoami")
    def whoami(ctx: Context) -> JsonResponse:
        return JsonResponse(ctx.state.require(SESSION))

    @app.error_handler
    def error_handler(ctx: Context, exc: Exception) -> Response:
        data = ctx.state.require(SESSION)
        data.clear()
        if error_user is not None:
            data["user"] = error_user
        return Response(status_code=500, headers={"Set-Cookie": "unrelated=1"})

    client = TestClient(app, base_url="https://example.com")
    initial_cookie = client.get("/login").headers.get("set-cookie", "").split(";", 1)[0]
    result = client.get("/", headers={"cookie": initial_cookie})
    cookies = result.headers.get_list("set-cookie")
    session_cookies = [cookie for cookie in cookies if cookie.startswith("session=")]
    assert result.status_code == 500 and cookies[0] == "unrelated=1"
    assert len(session_cookies) == int(
        initial_user is not None or error_user is not None
    )
    next_cookie = initial_cookie
    if session_cookies:
        parsed = SimpleCookie(session_cookies[0])["session"]
        assert parsed["secure"] and parsed["httponly"] and parsed["samesite"] == "none"
        assert (parsed["max-age"] == "0") == (error_user is None)
        next_cookie = session_cookies[0].split(";", 1)[0]
    assert client.get("/whoami", headers={"cookie": next_cookie}).json() == (
        {} if error_user is None else {"user": error_user}
    )


@pytest.mark.contract("MW-SESSION", "APP-ERROR")
@pytest.mark.parametrize("fail_error_handler", [False, True])
def test_session_clear_survives_error_handler_fallback(
    fail_error_handler: bool,
) -> None:
    app = App()
    app.use(session("secret"))

    @app.get("/login")
    def login(ctx: Context) -> str:
        ctx.state.require(SESSION)["user"] = "alice"
        return "ok"

    @app.get("/")
    def route(ctx: Context) -> Response:
        return Response(headers={"bad name": "invalid"})

    @app.error_handler
    def error_handler(ctx: Context, exc: Exception) -> Response:
        ctx.state.require(SESSION).clear()
        if fail_error_handler:
            raise ValueError("broken error handler")
        return Response(headers={"bad name": "invalid again"})

    client = TestClient(app)
    cookie = client.get("/login").headers["set-cookie"].split(";", 1)[0]
    result = client.get("/", headers={"cookie": cookie})
    assert result.status_code == 500
    cookies = result.headers.get_list("set-cookie")
    assert len(cookies) == 1 and SimpleCookie(cookies[0])["session"]["max-age"] == "0"


@pytest.mark.contract("MW-SESSION")
def test_outer_middleware_session_changes_are_finalized_after_chain() -> None:
    app = App()

    def outer(next_handler: Handler) -> Handler:
        async def handler(ctx: Context) -> Response:
            response = await next_handler(ctx)
            ctx.state.require(SESSION)["user"] = "bob"
            return response

        return handler

    app.use(outer, session("secret"))

    @app.get("/")
    def route(ctx: Context) -> str:
        ctx.state.require(SESSION)["user"] = "alice"
        return "ok"

    @app.get("/whoami")
    def whoami(ctx: Context) -> JsonResponse:
        return JsonResponse(ctx.state.require(SESSION))

    client = TestClient(app)
    result = client.get("/")
    cookie = result.headers["set-cookie"].split(";", 1)[0]
    assert client.get("/whoami", headers={"cookie": cookie}).json() == {"user": "bob"}
