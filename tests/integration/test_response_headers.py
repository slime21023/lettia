from collections.abc import AsyncGenerator

import pytest

from lettia import App, Context
from lettia.asgi import (
    HTTPSendEvent,
)
from lettia.response import JsonResponse, Response, ResponseValue, StreamResponse
from lettia.testing import TestClient
from tests.support.asgi import (
    http_receive,
    http_scope,
    http_sender,
    response_body,
)


@pytest.mark.contract("RESP-LENGTH")
@pytest.mark.parametrize("kind", ["plain", "json", "tuple", "stream"])
@pytest.mark.parametrize("method", ["GET", "HEAD"])
@pytest.mark.parametrize("status", [200, 204, 304])
def test_response_lengths_across_routes_and_body_suppression(
    kind: str, method: str, status: int
) -> None:
    app = App()
    payload = JsonResponse({"ok": True}).body if kind == "json" else b"abc"

    def route(ctx: Context) -> ResponseValue:
        length = "000" + str(len(payload))
        if kind == "stream":

            async def chunks() -> AsyncGenerator[bytes, None]:
                yield payload

            return StreamResponse(
                chunks(), status_code=status, headers={"Content-Length": length}
            )
        response = (
            JsonResponse({"ok": True}, status_code=status)
            if kind == "json"
            else Response(body=payload, status_code=status)
        )
        if ctx.method == "HEAD":
            response.body = b""
        if kind == "tuple":
            return response, status, {"Content-Length": length}
        response.set_header("Content-Length", length)
        return response

    app.add_route("GET", "/", route)
    app.add_route("HEAD", "/", route)
    response = TestClient(app).request(method, "/")
    assert response.status_code == status
    assert response.content == (payload if method == "GET" and status == 200 else b"")
    if status == 200:
        assert response.headers["content-length"] == "000" + str(len(payload))
    else:
        assert "content-length" not in response.headers


@pytest.mark.contract("RESP-HEAD")
@pytest.mark.parametrize("method", ["GET", "HEAD"])
async def test_app_invalid_error_headers_use_writer_fallback(method: str) -> None:
    app = App()
    app.add_route("GET", "/", lambda ctx: Response(headers={"x-bad": "bad\nvalue"}))
    app.set_error_handler(lambda ctx, exc: Response(headers={"x-error": "bad\nvalue"}))
    sent: list[HTTPSendEvent] = []

    await app(http_scope(method=method), http_receive([]), http_sender(sent))

    assert len(sent) == 2
    start, body = sent
    assert start["type"] == "http.response.start" and start["status"] == 500
    assert body["type"] == "http.response.body"
    assert body["body"] == (b"" if method == "HEAD" else b"Internal Server Error")


@pytest.mark.contract("RESP-HEAD")
def test_head_falls_back_to_get_without_sending_body() -> None:
    app = App()
    app.add_route("GET", "/hello", lambda ctx: "hello")
    response = TestClient(app).request("HEAD", "/hello")
    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-length"] == "5"


@pytest.mark.contract("RESP-LENGTH")
@pytest.mark.parametrize("bad_length", ["", "abc", "-1", "+1", "1.0", "1, 1", "１"])
@pytest.mark.parametrize("method", ["GET", "HEAD"])
async def test_stream_invalid_content_length_is_rejected_before_send(
    bad_length: str,
    method: str,
) -> None:
    app = App()
    closed: list[bool] = []
    sent: list[HTTPSendEvent] = []

    class Stream:
        def __aiter__(self) -> "Stream":
            return self

        async def __anext__(self) -> bytes:
            raise AssertionError("Invalid response must not read the stream")

        async def aclose(self) -> None:
            closed.append(True)

    @app.get("/")
    def route(ctx: Context) -> StreamResponse:
        return StreamResponse(Stream(), headers={"Content-Length": bad_length})

    async def send(message: HTTPSendEvent) -> None:
        if message["type"] == "http.response.start":
            assert message["status"] == 500
        sent.append(message)

    await app(http_scope(method=method), http_receive([]), send)
    assert bool(response_body(sent)) == (method == "GET")
    assert closed == [True]


@pytest.mark.contract("RESP-HEAD")
@pytest.mark.parametrize("response_type", ["plain", "json", "tuple", "stream", "error"])
async def test_invalid_headers_produce_one_valid_error_response(
    response_type: str,
) -> None:
    app = App()

    async def chunks() -> AsyncGenerator[bytes, None]:
        yield b"ok"

    @app.get("/")
    def route(ctx: Context) -> Response | tuple[str, int, dict[str, str]]:
        if response_type == "error":
            ctx.abort(400, "bad request", headers={"bad name": "value"})
        if response_type == "tuple":
            return "ok", 200, {"bad name": "value"}
        if response_type == "json":
            return JsonResponse({}, headers={"x-test": "bad\x00value"})
        if response_type == "stream":
            return StreamResponse(chunks(), headers={"bad name": "value"})
        return Response(headers={"bad name": "value"})

    sent: list[HTTPSendEvent] = []

    async def send(message: HTTPSendEvent) -> None:
        if message["type"] == "http.response.start":
            assert all(
                b" " not in name and b"\x00" not in value
                for name, value in message["headers"]
            )
        sent.append(message)

    await app(http_scope(), http_receive([]), send)

    start = sent[0]
    assert start["type"] == "http.response.start" and start["status"] == 500
    assert response_body(sent)
