from collections.abc import AsyncGenerator

import pytest

from lettia import SESSION, App, Context
from lettia.asgi import HTTPSendEvent
from lettia.errors import HTTPException
from lettia.middleware import Handler, cors, rate_limit, request_id, session, timeout
from lettia.response import Response, StreamResponse, TextResponse
from lettia.testing import TestClient
from tests.support.asgi import (
    http_receive,
    http_scope,
    response_body,
)


@pytest.mark.contract("APP-ERROR")
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

    sync_app = App()
    sync_app.set_error_handler(lambda ctx, exc: f"handled: {exc}")
    sync_app.add_route("GET", "/explode", explode)
    sync_response = TestClient(sync_app).get("/explode")
    assert sync_response.status_code == 200
    assert sync_response.text == "handled: boom"


@pytest.mark.contract("APP-ERROR", "MW-CORS", "MW-SESSION", "MW-REQUEST-ID")
@pytest.mark.parametrize(
    "origin", [None, "https://blocked.example", "https://frontend.example"]
)
@pytest.mark.parametrize("status", [200, 400, 404, 405, 429, 500])
def test_middleware_composition_preserves_error_headers_and_cookies(
    origin: str | None,
    status: int,
) -> None:
    app = App()
    app.use(
        cors(allow_origins=["https://frontend.example"], allow_credentials=True),
        session(secret_key="integration-secret"),
        request_id(generator=lambda: "new-id"),
        timeout(60),
        rate_limit(requests_per_minute=1),
    )

    def response(code: int) -> Response:
        return TextResponse(
            "result",
            status_code=code,
            headers={
                "Vary": "Accept-Encoding",
                "X-Request-ID": "old-id",
                "Set-Cookie": "app=1",
            },
        )

    def route(ctx: Context) -> Response:
        ctx.state.require(SESSION)["seen"] = True
        if status == 400:
            ctx.abort(400, "invalid")
        if status == 500:
            raise RuntimeError("failure")
        return response(200)

    def errors(ctx: Context, exc: Exception) -> Response:
        code = exc.status_code if isinstance(exc, HTTPException) else 500
        result = response(code)
        if isinstance(exc, HTTPException):
            for name, value in (exc.headers or {}).items():
                result.set_header(name, value)
        return result

    app.add_route("GET", "/", route)
    app.set_error_handler(errors)
    client = TestClient(app)
    headers = {"Origin": origin} if origin else {}
    if status == 429:
        client.get("/", headers=headers)
    result = client.request(
        "POST" if status == 405 else "GET",
        "/missing" if status == 404 else "/",
        headers=headers,
    )

    assert result.status_code == status
    assert result.headers.get_list("x-request-id") == ["new-id"]
    assert {token.strip() for token in result.headers["vary"].split(",")} == {
        "Accept-Encoding",
        "Origin",
    }
    assert result.headers.get("access-control-allow-origin") == (
        origin if origin == "https://frontend.example" else None
    )
    cookies = result.headers.get_list("set-cookie")
    assert cookies[0] == "app=1"
    if status in (200, 400, 500):
        assert len(cookies) == 2 and cookies[1].startswith("session=")
    if status == 429:
        assert int(result.headers["retry-after"]) > 0


@pytest.mark.contract("APP-ERROR", "MW-CORS", "MW-SESSION", "MW-REQUEST-ID")
@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize(
    "failure", ["plain", "stream", "outer_middleware", "outer_exception"]
)
@pytest.mark.parametrize("replacement", ["default", "custom", "invalid"])
@pytest.mark.contract("RESP-POLICY")
def test_writer_replacement_replays_response_policies_without_rerunning_route(
    method: str, failure: str, replacement: str
) -> None:
    app = App()
    calls: list[str] = []

    def outer(next_handler: Handler) -> Handler:
        async def handler(ctx: Context) -> Response:
            calls.append("middleware")
            response = await next_handler(ctx)
            if failure == "outer_middleware":
                response.headers["bad name"] = "invalid"
            if failure == "outer_exception":
                raise ValueError("middleware failed after next_handler")
            return response

        return handler

    def new_id() -> str:
        calls.append("request_id")
        return "stable-id"

    app.use(
        outer,
        cors(allow_origins=["https://example.com"], allow_credentials=True),
        request_id(generator=new_id),
        session(
            "secret", cookie_name="__Host-session", https_only=True, same_site="none"
        ),
        rate_limit(requests_per_minute=1),
    )

    @app.get("/")
    def route(ctx: Context) -> Response:
        calls.append("route")
        ctx.state.require(SESSION)["user"] = "alice"
        headers = (
            {}
            if failure in ("outer_middleware", "outer_exception")
            else {"bad name": "invalid"}
        )
        if failure == "stream":

            async def chunks() -> AsyncGenerator[bytes, None]:
                yield b"unreachable"

            return StreamResponse(chunks(), headers=headers)
        return Response(headers=headers)

    if replacement != "default":

        @app.error_handler
        def error_handler(ctx: Context, exc: Exception) -> Response:
            calls.append("error")
            headers = {"Vary": "Accept-Encoding", "Set-Cookie": "error=1"}
            if replacement == "invalid":
                headers["bad name"] = "invalid again"
            return Response(status_code=502, body=b"error", headers=headers)

    result = TestClient(app, base_url="https://example.com").request(
        method, "/", headers={"origin": "https://example.com"}
    )

    assert result.status_code == (502 if replacement == "custom" else 500)
    assert result.headers["access-control-allow-origin"] == "https://example.com"
    assert result.headers["access-control-allow-credentials"] == "true"
    assert result.headers["x-request-id"] == "stable-id"
    assert result.headers["vary"] == (
        "Accept-Encoding, Origin" if replacement == "custom" else "Origin"
    )
    cookies = result.headers.get_list("set-cookie")
    session_cookies = [
        cookie for cookie in cookies if cookie.startswith("__Host-session=")
    ]
    assert len(session_cookies) == 1
    assert "; Secure; HttpOnly; SameSite=none" in session_cookies[0]
    assert len(cookies) == (2 if replacement == "custom" else 1)
    assert bool(result.content) == (method == "GET")
    errors = [] if replacement == "default" else ["error"]
    if failure == "outer_exception" and replacement == "invalid":
        errors.append("error")
    assert calls == ["middleware", "request_id", "route", *errors]


@pytest.mark.contract("APP-ERROR", "RESP-STREAM")
@pytest.mark.contract("RESP-POLICY")
async def test_failed_response_policies_preserve_fallback_and_close_stream() -> None:
    app = App()
    app.use(
        cors(allow_origins=["https://example.com"]),
        request_id(generator=lambda: "bad\r\nid"),
        session("secret", same_site="invalid"),
    )
    closed: list[bool] = []

    class Stream:
        def __aiter__(self) -> "Stream":
            return self

        async def __anext__(self) -> bytes:
            raise AssertionError("Invalid policies must not start the stream")

        async def aclose(self) -> None:
            closed.append(True)

    @app.get("/")
    def route(ctx: Context) -> StreamResponse:
        ctx.state.require(SESSION)["user"] = "alice"
        return StreamResponse(Stream())

    sent: list[HTTPSendEvent] = []

    async def send(message: HTTPSendEvent) -> None:
        sent.append(message)

    await app(
        http_scope(headers=[(b"origin", b"https://example.com")]),
        http_receive([]),
        send,
    )
    assert response_body(sent) and closed == [True]
    start = sent[0]
    assert start["type"] == "http.response.start" and start["status"] == 500
    assert (b"access-control-allow-origin", b"https://example.com") in start["headers"]
    assert not any(
        name in (b"set-cookie", b"x-request-id") for name, _ in start["headers"]
    )


@pytest.mark.contract("APP-ERROR")
@pytest.mark.parametrize(
    "failure",
    ["validation", "error_validation", "start", "body", "end", "source", "cleanup"],
)
async def test_error_response_resources_are_created_only_before_start(
    failure: str,
) -> None:
    app = App()
    events: list[str] = []
    sent: list[HTTPSendEvent] = []
    attempts: list[HTTPSendEvent] = []

    class Stream:
        def __init__(self, name: str) -> None:
            self.name = name
            self.emitted = False
            events.append(name + " acquired")

        def __aiter__(self) -> "Stream":
            return self

        async def __anext__(self) -> bytes:
            if self.emitted:
                if self.name == "route" and failure == "source":
                    raise LookupError("source failed")
                raise StopAsyncIteration
            self.emitted = True
            return b"data"

        async def aclose(self) -> None:
            events.append(self.name + " closed")
            if self.name == "route" and failure == "cleanup":
                raise ValueError("cleanup failed")

    @app.get("/")
    def route(ctx: Context) -> StreamResponse:
        invalid = failure in ("validation", "error_validation")
        return StreamResponse(
            Stream("route"), headers={"bad name": "invalid"} if invalid else {}
        )

    @app.error_handler
    def error_handler(ctx: Context, exc: Exception) -> Response:
        return StreamResponse(
            Stream("error"),
            status_code=500,
            headers={"bad name": "invalid"} if failure == "error_validation" else {},
        )

    async def send(message: HTTPSendEvent) -> None:
        attempts.append(message)
        phase = (
            "start"
            if message["type"] == "http.response.start"
            else "body"
            if message.get("more_body")
            else "end"
        )
        if failure == phase:
            raise OSError("transport failed")
        sent.append(message)

    await app(http_scope(), http_receive([]), send)
    expected = ["route acquired", "route closed"]
    if failure in ("validation", "error_validation"):
        expected += ["error acquired", "error closed"]
        assert response_body(sent)
    assert events == expected
    assert sum(message["type"] == "http.response.start" for message in attempts) == 1
