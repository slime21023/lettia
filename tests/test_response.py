from collections.abc import AsyncGenerator

import pytest
from asgi_helpers import http_sender

from lettia.asgi import HTTPSendEvent
from lettia.response import (
    JsonResponse,
    Response,
    ResponseWriter,
    StreamResponse,
    TextResponse,
    normalize_response,
)


def test_response_headers_and_cookies() -> None:
    resp = Response(status_code=200)
    resp.set_header("X-Custom-Header", "test-val")
    resp.set_cookie("session", "abc", max_age=3600, httponly=True)

    assert resp.headers["x-custom-header"] == "test-val"
    assert "session=abc" in resp.headers["set-cookie"]
    assert "HttpOnly" in resp.headers["set-cookie"]


def test_response_delete_cookie_and_header_validation() -> None:
    resp = Response()
    resp.delete_cookie("session")

    assert "session=; Path=/; Max-Age=0" in resp.headers["set-cookie"]

    with pytest.raises(ValueError, match="cannot contain newlines"):
        resp.set_header("x-test", "safe\r\nInjected: true")
    with pytest.raises(ValueError, match="cannot contain newlines"):
        resp.set_cookie("session", "safe\nInjected")


def test_response_cookie_optional_attributes_and_validation() -> None:
    response = Response()
    response.set_cookie(
        "token",
        "value",
        domain="example.com",
        expires="Wed, 21 Oct 2030 07:28:00 GMT",
        secure=True,
        samesite="strict",
    )
    cookie = response.headers["set-cookie"]
    assert "Domain=example.com" in cookie
    assert "Expires=Wed, 21 Oct 2030 07:28:00 GMT" in cookie
    assert "Secure" in cookie
    assert "SameSite=strict" in cookie

    with pytest.raises(ValueError, match="cannot contain newlines"):
        response.set_cookie("token", "value", domain="bad\nexample.com")
    with pytest.raises(ValueError, match="cannot contain newlines"):
        response.set_cookie("token", "value", samesite="strict\ninvalid")


@pytest.mark.asyncio
async def test_response_writer_rejects_non_latin_headers() -> None:
    sent_messages: list[HTTPSendEvent] = []
    writer = ResponseWriter(send=http_sender(sent_messages))
    with pytest.raises(ValueError, match="Latin-1"):
        await writer.write(Response(headers={"x-emoji": "🙂"}))
    assert sent_messages == []


def test_response_types() -> None:
    text_resp = TextResponse("Hello World", status_code=201)
    assert text_resp.status_code == 201
    assert text_resp.body == b"Hello World"
    assert text_resp.media_type == "text/plain; charset=utf-8"

    json_resp = JsonResponse({"msg": "ok"}, status_code=200)
    assert json_resp.body == b'{"msg": "ok"}'
    assert json_resp.media_type == "application/json"


def test_normalize_response() -> None:
    r1 = normalize_response("hello")
    assert isinstance(r1, TextResponse)
    assert r1.body == b"hello"

    r2 = normalize_response({"a": 1})
    assert isinstance(r2, JsonResponse)
    assert r2.body == b'{"a": 1}'

    r3 = normalize_response(("created", 201, {"x-test": "1"}))
    assert r3.status_code == 201
    assert r3.headers["x-test"] == "1"

    bytes_response = normalize_response(b"payload")
    list_response = normalize_response(["one", 2])
    tuple_response = normalize_response(("accepted", 202))
    assert bytes_response.media_type == "application/octet-stream"
    assert isinstance(list_response, JsonResponse)
    assert tuple_response.status_code == 202


@pytest.mark.asyncio
async def test_response_writer_committed_safety() -> None:
    sent_messages: list[HTTPSendEvent] = []
    writer = ResponseWriter(send=http_sender(sent_messages))
    assert not writer.committed

    resp = TextResponse("OK", status_code=200)
    await writer.write(resp)

    assert writer.committed
    assert len(sent_messages) == 2


@pytest.mark.asyncio
async def test_response_writer_head_request_has_no_body() -> None:
    sent_messages: list[HTTPSendEvent] = []
    writer = ResponseWriter(send=http_sender(sent_messages), head_only=True)
    await writer.write(TextResponse("body"))
    assert sent_messages[-1] == {
        "type": "http.response.body",
        "body": b"",
        "more_body": False,
    }


@pytest.mark.asyncio
async def test_response_writer_preserves_explicit_content_type() -> None:
    sent_messages: list[HTTPSendEvent] = []
    writer = ResponseWriter(send=http_sender(sent_messages))
    await writer.write(Response(headers={"content-type": "application/custom"}))
    assert sent_messages[0]["type"] == "http.response.start"
    assert (
        sent_messages[0]["headers"].count((b"content-type", b"application/custom")) == 1
    )
    assert sent_messages[0]["type"] == "http.response.start"
    assert sent_messages[0]["status"] == 200

    # Write second time should be ignored because committed is True
    await writer.write(TextResponse("Duplicate", status_code=500))
    assert len(sent_messages) == 2


@pytest.mark.asyncio
async def test_stream_response() -> None:
    async def stream_gen() -> AsyncGenerator[bytes, None]:
        yield b"part1"
        yield b"part2"

    resp = StreamResponse(generator=stream_gen())
    sent_messages: list[HTTPSendEvent] = []
    writer = ResponseWriter(send=http_sender(sent_messages))
    await writer.write(resp)

    assert len(sent_messages) == 4
    assert sent_messages[1]["type"] == "http.response.body"
    assert sent_messages[1]["body"] == b"part1"
    assert sent_messages[2]["type"] == "http.response.body"
    assert sent_messages[2]["body"] == b"part2"


@pytest.mark.asyncio
async def test_stream_failure_terminates_response_and_closes_iterator() -> None:
    closed = False

    async def stream_gen() -> AsyncGenerator[bytes, None]:
        nonlocal closed
        try:
            yield b"part1"
            raise RuntimeError("stream failed")
        finally:
            closed = True

    sent_messages: list[HTTPSendEvent] = []

    async def mock_send(message: HTTPSendEvent) -> None:
        sent_messages.append(message)

    writer = ResponseWriter(send=mock_send)

    with pytest.raises(RuntimeError, match="stream failed"):
        await writer.write(StreamResponse(stream_gen()))

    assert closed
    assert sent_messages[-1] == {
        "type": "http.response.body",
        "body": b"",
        "more_body": False,
    }


@pytest.mark.asyncio
async def test_response_writer_rejects_unsafe_direct_headers() -> None:
    sent_messages: list[HTTPSendEvent] = []
    writer = ResponseWriter(send=http_sender(sent_messages))
    response = Response(headers={"x-test": "safe\r\nInjected: true"})

    with pytest.raises(ValueError, match="cannot contain newlines"):
        await writer.write(response)

    with pytest.raises(ValueError, match="Latin-1"):
        await writer.write(Response(headers={"x-emoji": "🙂"}))

    assert sent_messages == []


@pytest.mark.asyncio
async def test_response_writer_validates_content_length_and_multiple_cookies() -> None:
    sent_messages: list[HTTPSendEvent] = []
    writer = ResponseWriter(send=http_sender(sent_messages))
    invalid_response = Response(headers={"content-length": "99"}, body=b"OK")
    with pytest.raises(ValueError, match="does not match"):
        await writer.write(invalid_response)

    response = Response(body=b"OK")
    response.set_cookie("first", "one")
    response.set_cookie("second", "two")
    await writer.write(response)

    assert sent_messages[0]["type"] == "http.response.start"
    headers = sent_messages[0]["headers"]
    assert headers.count((b"content-length", b"2")) == 1
    assert (b"set-cookie", b"first=one; Path=/; SameSite=lax") in headers
    assert (b"set-cookie", b"second=two; Path=/; SameSite=lax") in headers
