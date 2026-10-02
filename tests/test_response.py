import asyncio
import json
from collections.abc import AsyncGenerator

import pytest
from asgi_helpers import http_sender, response_body
from hypothesis import example, given
from hypothesis import strategies as st
from strategies import JSON_VALUES, PAYLOADS, TEXT

from lettia import JSONValue
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


@given(
    payload=PAYLOADS,
    status=st.sampled_from([200, 201, 204, 206, 304, 400, 500]),
    head=st.booleans(),
)
@example(payload=b"hello", status=304, head=False)
async def test_response_events_follow_method_and_status_semantics(
    payload: bytes,
    status: int,
    head: bool,
) -> None:
    sent: list[HTTPSendEvent] = []
    writer = ResponseWriter(http_sender(sent), head_only=head)
    await writer.write(Response(status_code=status, body=payload))
    assert response_body(sent) == (b"" if head or status in (204, 304) else payload)
    start = sent[0]
    assert start["type"] == "http.response.start"
    headers = dict(start["headers"])
    if status in (204, 304):
        assert b"content-length" not in headers
    else:
        assert headers[b"content-length"] == str(len(payload)).encode()
    original = sent.copy()
    await writer.write(Response(body=b"second"))
    assert sent == original


@given(value=JSON_VALUES)
async def test_json_response_round_trip(value: JSONValue) -> None:
    sent: list[HTTPSendEvent] = []
    await ResponseWriter(http_sender(sent)).write(JsonResponse(value))
    assert json.loads(response_body(sent)) == value


@given(value=TEXT, status=st.integers(200, 599))
def test_response_normalization_preserves_text_and_metadata(
    value: str, status: int
) -> None:
    response = normalize_response((value, status, {"X-Test": "value"}))
    assert response.body.decode() == value and response.status_code == status
    assert response.headers["x-test"] == "value"
    assert normalize_response(response) is response


@given(
    chunks=st.lists(st.binary(max_size=64), max_size=20),
    failure=st.sampled_from(["none", "runtime", "timeout"]),
    with_deadline=st.booleans(),
)
@example(chunks=[b"first"], failure="timeout", with_deadline=False)
@example(chunks=[b"first"], failure="timeout", with_deadline=True)
async def test_stream_finishes_once_and_preserves_upstream_errors(
    chunks: list[bytes],
    failure: str,
    with_deadline: bool,
) -> None:
    closed = False

    async def stream() -> AsyncGenerator[bytes, None]:
        nonlocal closed
        try:
            for chunk in chunks:
                yield chunk
            if failure == "runtime":
                raise RuntimeError("upstream")
            if failure == "timeout":
                raise TimeoutError("upstream")
        finally:
            closed = True

    sent: list[HTTPSendEvent] = []
    writer = ResponseWriter(http_sender(sent))
    response = StreamResponse(stream())
    deadline = asyncio.get_running_loop().time() + 60 if with_deadline else None
    if failure == "none":
        await writer.write(response, deadline=deadline)
    else:
        error = TimeoutError if failure == "timeout" else RuntimeError
        with pytest.raises(error, match="upstream"):
            await writer.write(response, deadline=deadline)
    assert closed
    assert response_body(sent) == b"".join(chunks)


@given(
    chunks=st.lists(st.binary(max_size=64), min_size=1, max_size=10),
    cancel=st.booleans(),
)
async def test_stream_transport_failure_closes_without_retry(
    chunks: list[bytes],
    cancel: bool,
) -> None:
    closed = False
    attempts = 0

    async def stream() -> AsyncGenerator[bytes, None]:
        nonlocal closed
        try:
            for chunk in chunks:
                yield chunk
        finally:
            closed = True

    async def send(message: HTTPSendEvent) -> None:
        nonlocal attempts
        attempts += 1
        if message["type"] == "http.response.body":
            if cancel:
                raise asyncio.CancelledError
            raise OSError("disconnected")

    iterator = stream()
    error = asyncio.CancelledError if cancel else OSError
    with pytest.raises(error):
        await ResponseWriter(send).write(StreamResponse(iterator))
    assert closed and attempts == 2


@given(after_headers=st.booleans())
async def test_response_deadline_only_finishes_after_commit(
    after_headers: bool,
) -> None:
    from lettia.response import ResponseTimeout

    sent: list[HTTPSendEvent] = []
    closed = False

    async def send(message: HTTPSendEvent) -> None:
        if not after_headers:
            await asyncio.Event().wait()
        sent.append(message)

    async def stream() -> AsyncGenerator[bytes, None]:
        nonlocal closed
        try:
            await asyncio.Event().wait()
            yield b"unreachable"
        finally:
            closed = True

    writer = ResponseWriter(send)
    response = StreamResponse(stream())
    deadline = asyncio.get_running_loop().time() - 1
    if after_headers:
        await writer.write(response, deadline=deadline)
        assert response_body(sent) == b""
        assert closed
    else:
        with pytest.raises(ResponseTimeout):
            await writer.write(response, deadline=deadline)
        assert not sent and not writer.committed


@given(terminal=st.booleans())
async def test_deadline_during_send_never_retries_transport(terminal: bool) -> None:
    attempts: list[HTTPSendEvent] = []
    closed = False

    async def stream() -> AsyncGenerator[bytes, None]:
        nonlocal closed
        try:
            if not terminal:
                yield b"chunk"
        finally:
            closed = True

    async def send(message: HTTPSendEvent) -> None:
        attempts.append(message)
        if len(attempts) == 2:
            await asyncio.Event().wait()

    with pytest.raises(TimeoutError):
        await ResponseWriter(send).write(
            StreamResponse(stream()), deadline=asyncio.get_running_loop().time() - 1
        )
    assert len(attempts) == 2 and closed
