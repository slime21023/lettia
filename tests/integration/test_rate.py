import pytest

from lettia import App, Context
from lettia.asgi import HTTPSendEvent
from lettia.middleware import rate_limit
from lettia.testing import TestClient
from tests.support.asgi import http_receive, http_scope, http_sender


@pytest.mark.contract("MW-RATE")
@pytest.mark.parametrize("client_kind", ["missing", "none", "address"])
async def test_rate_limit_forwarded_headers_cannot_reset_bucket(
    client_kind: str,
) -> None:
    app = App()
    app.use(rate_limit(requests_per_minute=1))
    app.add_route("GET", "/", lambda ctx: "ok")
    statuses: list[int] = []
    for forwarded in (b"192.0.2.1", b"192.0.2.2"):
        scope = http_scope(headers=[(b"x-forwarded-for", forwarded)])
        if client_kind == "none":
            scope["client"] = None
        elif client_kind == "address":
            scope["client"] = ("127.0.0.1", 1234)
        sent: list[HTTPSendEvent] = []
        await app(scope, http_receive([]), http_sender(sent))
        start = sent[0]
        assert start["type"] == "http.response.start"
        statuses.append(start["status"])

    assert statuses == [200, 429]


@pytest.mark.contract("MW-RATE")
async def test_rate_limit_unknown_and_loopback_have_separate_buckets() -> None:
    app = App()
    app.use(rate_limit(requests_per_minute=1))
    app.add_route("GET", "/", lambda ctx: "ok")
    for client in (None, ("127.0.0.1", 1234)):
        scope = http_scope()
        scope["client"] = client
        sent: list[HTTPSendEvent] = []
        await app(scope, http_receive([]), http_sender(sent))
        start = sent[0]
        assert start["type"] == "http.response.start" and start["status"] == 200


@pytest.mark.contract("MW-RATE")
def test_rate_limit_custom_key_remains_authoritative() -> None:
    app = App()
    app.use(
        rate_limit(
            requests_per_minute=1, key_func=lambda ctx: ctx.header("x-key") or "unknown"
        )
    )
    app.add_route("GET", "/", lambda ctx: "ok")
    client = TestClient(app)

    assert [
        client.get("/", headers={"x-key": key}).status_code for key in ("a", "a", "b")
    ] == [200, 429, 200]


@pytest.mark.contract("MW-RATE")
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
