import base64
import json
from importlib import import_module
from unittest.mock import patch

import pytest
from hypothesis import example, given
from hypothesis import strategies as st
from strategies import JSON_OBJECTS

from lettia import SESSION, App, Context
from lettia.asgi import JSONValue
from lettia.middleware import MemoryRateLimiter, rate_limit, session
from lettia.middleware.session import _sign, _unsign
from lettia.testing import TestClient

rate_limit_module = import_module("lettia.middleware.rate_limit")


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


def test_rate_limiter_purges_inactive_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = 0.0
    monkeypatch.setattr(rate_limit_module.time, "monotonic", lambda: clock)
    limiter = MemoryRateLimiter(requests_per_minute=10)
    limiter.is_allowed("inactive")

    clock = 61.0
    for index in range(255):
        limiter.is_allowed(f"active-{index}")

    assert "inactive" not in limiter._history
    assert "active-254" in limiter._history


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


def test_signed_session_round_trip_allows_dots_in_payload() -> None:
    payload = b'{"user_id":"user.999"}'
    signed = _sign(payload, b"secret")

    assert _unsign(signed, b"secret") == payload


@given(
    data=JSON_OBJECTS, lifetime=st.integers(1, 1000), delta=st.sampled_from([-1, 0, 1])
)
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


@given(data=JSON_OBJECTS)
def test_session_rejects_legacy_cookies(data: dict[str, JSONValue]) -> None:
    app = App()
    app.use(session("test-secret"))
    app.add_route("GET", "/", lambda ctx: ctx.state.require(SESSION))
    legacy = _sign(json.dumps({"payload": data}).encode(), b"test-secret")
    assert (
        TestClient(app).get("/", headers={"cookie": "session=" + legacy}).json() == {}
    )


@given(lifetime=st.integers(max_value=0))
def test_session_rejects_nonpositive_lifetime(lifetime: int) -> None:
    with pytest.raises(ValueError):
        session("test-secret", max_age=lifetime)


@given(
    limit=st.integers(1, 10),
    operations=st.lists(
        st.tuples(
            st.integers(0, 2), st.sampled_from([0.0, 0.5, 1.0, 59.5, 60.0, 61.0])
        ),
        min_size=1,
        max_size=50,
    ),
)
def test_rate_limit_matches_independent_window_model(
    limit: int,
    operations: list[tuple[int, float]],
) -> None:
    limiter = MemoryRateLimiter(limit)
    other = MemoryRateLimiter(limit)
    now = 100.0
    accepted: list[tuple[int, float]] = []
    for index, (key, advance) in enumerate(operations):
        now += advance
        active = [at for owner, at in accepted if owner == key and at > now - 60]
        expected = len(active) < limit
        with patch.object(rate_limit_module.time, "monotonic", return_value=now):
            allowed, retry = limiter.is_allowed(str(key))
            assert allowed == expected
            if allowed:
                accepted.append((key, now))
                assert retry == 0
            else:
                assert retry >= 1
            assert other.is_allowed(str(index))[0]


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
