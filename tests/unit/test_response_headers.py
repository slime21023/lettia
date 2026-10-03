from collections.abc import AsyncGenerator

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from lettia.asgi import HTTPSendEvent
from lettia.response import (
    JsonResponse,
    Response,
    ResponseWriter,
    StreamResponse,
    TextResponse,
    normalize_response,
)
from tests.support.asgi import http_sender, response_body
from tests.support.strategies import PAYLOADS


@pytest.mark.contract("RESP-LENGTH")
@given(payload=PAYLOADS, zeros=st.integers(0, 5000), head=st.booleans())
async def test_content_length_numeric_equivalence_preserves_response(
    payload: bytes, zeros: int, head: bool
) -> None:
    length = "0" * zeros + str(len(payload))
    sent: list[HTTPSendEvent] = []
    await ResponseWriter(http_sender(sent), head_only=head).write(
        Response(body=payload, headers={"Content-Length": length})
    )
    start = sent[0]
    assert start["type"] == "http.response.start"
    assert start["headers"].count((b"content-length", length.encode())) == 1
    assert response_body(sent) == (b"" if head else payload)


@pytest.mark.contract("RESP-LENGTH")
@pytest.mark.parametrize("head", [False, True])
@pytest.mark.parametrize("length", ["000123", "0" * 5000 + "123"])
async def test_content_length_representation_length_is_independent_for_head(
    head: bool, length: str
) -> None:
    sent: list[HTTPSendEvent] = []
    writer = ResponseWriter(http_sender(sent), head_only=head)
    response = Response(headers={"content-length": length})
    if head:
        await writer.write(response)
        start = sent[0]
        assert start["type"] == "http.response.start"
        assert start["headers"] == [
            (b"content-length", length.encode()),
            (b"content-type", b"text/plain; charset=utf-8"),
        ]
        assert response_body(sent) == b""
    else:
        with pytest.raises(ValueError, match="does not match"):
            await writer.write(response)
        assert not sent


@pytest.mark.contract("RESP-HEAD")
@pytest.mark.parametrize(
    "headers",
    [
        {"content-length": "-1"},
        {"content-length": "1, 1"},
        {"content-length": "1", "Content-Length": "1"},
    ],
)
async def test_head_still_rejects_invalid_or_duplicate_lengths(
    headers: dict[str, str],
) -> None:
    sent: list[HTTPSendEvent] = []
    with pytest.raises(ValueError):
        await ResponseWriter(http_sender(sent), head_only=True).write(
            Response(headers=headers)
        )
    assert not sent


@pytest.mark.contract("RESP-HEADERS")
@pytest.mark.asyncio
async def test_response_writer_rejects_non_latin_headers() -> None:
    sent_messages: list[HTTPSendEvent] = []
    writer = ResponseWriter(send=http_sender(sent_messages))
    with pytest.raises(ValueError, match="Latin-1"):
        await writer.write(Response(headers={"x-emoji": "🙂"}))
    assert sent_messages == []


@pytest.mark.contract("RESP-HEAD")
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


@pytest.mark.contract("RESP-HEADERS")
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


@pytest.mark.contract("RESP-HEAD")
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


@pytest.mark.contract("RESP-LENGTH")
@given(
    payload=PAYLOADS,
    status=st.sampled_from([200, 204, 206, 304, 500]),
    head=st.booleans(),
    leading_zeroes=st.integers(0, 3),
)
async def test_stream_content_length_preserves_valid_representation_headers(
    payload: bytes,
    status: int,
    head: bool,
    leading_zeroes: int,
) -> None:
    async def chunks() -> AsyncGenerator[bytes, None]:
        yield payload

    length = "0" * leading_zeroes + str(len(payload))
    sent: list[HTTPSendEvent] = []
    await ResponseWriter(http_sender(sent), head_only=head).write(
        StreamResponse(chunks(), status_code=status, headers={"Content-Length": length})
    )
    assert response_body(sent) == (b"" if head or status in (204, 304) else payload)
    start = sent[0]
    assert start["type"] == "http.response.start"
    headers = dict(start["headers"])
    if status in (204, 304):
        assert b"content-length" not in headers
    else:
        assert headers[b"content-length"] == length.encode("ascii")


@pytest.mark.contract("RESP-HEADERS")
@given(
    uppercase=st.lists(st.booleans(), min_size=12, max_size=12),
    response_type=st.sampled_from(["plain", "text", "json", "stream", "tuple"]),
)
async def test_header_override_all_response_types_replaces_case_variants(
    uppercase: list[bool],
    response_type: str,
) -> None:
    name = "".join(
        char.upper() if upper else char
        for char, upper in zip("content-type", uppercase, strict=True)
    )
    headers = {name: "old/type", "CONTENT-TYPE": "another/type"}

    async def stream() -> AsyncGenerator[bytes, None]:
        yield b"ok"

    if response_type == "text":
        response = TextResponse("ok", headers=headers)
    elif response_type == "json":
        response = JsonResponse({"ok": True}, headers=headers)
    elif response_type == "stream":
        response = StreamResponse(stream(), headers=headers)
    else:
        response = Response(body=b"ok", headers=headers)
    if response_type == "tuple":
        response = normalize_response((response, 200, {"Content-Type": "new/type"}))
    else:
        response.set_header("Content-Type", "new/type")
    sent: list[HTTPSendEvent] = []

    await ResponseWriter(http_sender(sent)).write(response)

    start = sent[0]
    assert start["type"] == "http.response.start"
    assert [value for key, value in start["headers"] if key == b"content-type"] == [
        b"new/type"
    ]


@pytest.mark.contract("RESP-HEADERS")
@pytest.mark.parametrize("value", ["bad\rvalue", "bad\nvalue", "🙂"])
def test_header_invalid_override_leaves_headers_unchanged(value: str) -> None:
    response = Response(headers={"X-Test": "old", "x-test": "older"})
    before = response.headers.copy()

    with pytest.raises(ValueError):
        response.set_header("X-Test", value)

    assert response.headers == before


@pytest.mark.contract("RESP-LENGTH")
async def test_duplicate_content_length_is_rejected_before_start() -> None:
    sent: list[HTTPSendEvent] = []
    response = Response(
        body=b"ok", headers={"Content-Length": "2", "content-length": "2"}
    )

    with pytest.raises(ValueError, match="multiple Content-Length"):
        await ResponseWriter(http_sender(sent)).write(response)

    assert not sent


@pytest.mark.contract("RESP-HEADERS")
@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("", "ok"),
        ("bad header", "ok"),
        ("x:bad", "ok"),
        ("é", "ok"),
        ("x-test", "a\x00b"),
        ("x-test", "a\x1fb"),
        ("x-test", "a\x7fb"),
    ],
)
async def test_invalid_header_grammar_is_rejected_before_mutation_or_send(
    name: str, value: str
) -> None:
    response = Response(headers={"X-Test": "original"})
    with pytest.raises(ValueError, match="HTTP header"):
        response.set_header(name, value)
    assert response.headers == {"X-Test": "original"}

    sent: list[HTTPSendEvent] = []
    writer = ResponseWriter(http_sender(sent))
    with pytest.raises(ValueError, match="HTTP header"):
        await writer.write(Response(headers={name: value}))
    assert not sent
    await writer.write(TextResponse("fallback", status_code=500))
    assert response_body(sent) == b"fallback"


@pytest.mark.contract("RESP-HEADERS", "RESP-LENGTH", "RESP-COOKIES")
def test_header_rules_validate_without_response_or_writer() -> None:
    from lettia._headers import build_headers, cookie_value, validate_header_component

    with pytest.raises(ValueError):
        validate_header_component("bad\r\nvalue", "value")
    assert cookie_value("a", "b") == "a=b; Path=/; SameSite=lax"
    assert build_headers({"Set-Cookie": "a=1\nb=2"}, "", 200, 2, False, False) == [
        (b"set-cookie", b"a=1"),
        (b"set-cookie", b"b=2"),
        (b"content-length", b"2"),
    ]
    with pytest.raises(ValueError, match="does not match"):
        build_headers({"Content-Length": "3"}, "", 200, 2, False, False)
