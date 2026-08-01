from collections.abc import AsyncGenerator
from typing import Any

import pytest

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


@pytest.mark.asyncio
async def test_response_writer_committed_safety() -> None:
    sent_messages: list[dict[str, Any]] = []

    async def mock_send(message: dict[str, Any]) -> None:
        sent_messages.append(message)

    writer = ResponseWriter(send=mock_send)
    assert not writer.committed

    resp = TextResponse("OK", status_code=200)
    await writer.write(resp)

    assert writer.committed
    assert len(sent_messages) == 2
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
    sent_messages: list[dict[str, Any]] = []

    async def mock_send(message: dict[str, Any]) -> None:
        sent_messages.append(message)

    writer = ResponseWriter(send=mock_send)
    await writer.write(resp)

    assert len(sent_messages) == 4
    assert sent_messages[1]["body"] == b"part1"
    assert sent_messages[2]["body"] == b"part2"
