import asyncio
import logging
from collections.abc import AsyncGenerator
from unittest.mock import patch

import pytest
from asgi_helpers import http_context, http_receive, http_scope, http_sender
from hypothesis import example, given
from hypothesis import strategies as st

from lettia import App
from lettia.asgi import HTTPSendEvent
from lettia.context import Context
from lettia.errors import HTTPException
from lettia.middleware import Handler, build_chain, cors, request_id
from lettia.middleware.body_limit import body_limit
from lettia.middleware.logger import request_logger
from lettia.middleware.recover import recover
from lettia.middleware.timeout import timeout
from lettia.response import Response, StreamResponse, TextResponse


@pytest.mark.asyncio
async def test_recover_middleware() -> None:
    async def failing_handler(ctx: Context) -> Response:
        raise ValueError("Something went wrong")

    recover_mw = recover()
    chain = recover_mw(failing_handler)

    ctx = http_context()
    with pytest.raises(ValueError, match="Something went wrong"):
        await chain(ctx)


@pytest.mark.asyncio
async def test_logger_middleware() -> None:
    logged_messages: list[str] = []

    def mock_log(msg: str) -> None:
        logged_messages.append(msg)

    async def ok_handler(ctx: Context) -> Response:
        return TextResponse("OK", status_code=200)

    log_mw = request_logger(log_func=mock_log)
    chain = log_mw(ok_handler)

    ctx = http_context(path="/test-log")
    await chain(ctx)

    assert len(logged_messages) == 1
    assert "GET /test-log -> 200" in logged_messages[0]


@pytest.mark.asyncio
async def test_body_limit_middleware() -> None:
    async def handler(ctx: Context) -> Response:
        return TextResponse("OK")

    limit_mw = body_limit(max_bytes=10)
    chain = limit_mw(handler)

    # Test Header exceeding limit
    ctx_header_over = http_context(method="POST", headers=[(b"content-length", b"100")])

    with pytest.raises(HTTPException) as exc_info:
        await chain(ctx_header_over)
    assert exc_info.value.status_code == 413


@pytest.mark.asyncio
async def test_body_limit_rejects_invalid_content_length() -> None:
    async def handler(ctx: Context) -> Response:
        return TextResponse("OK")

    chain = body_limit(max_bytes=10)(handler)
    ctx = http_context(method="POST", headers=[(b"content-length", b"invalid")])

    with pytest.raises(HTTPException, match="must be an integer") as exc_info:
        await chain(ctx)

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_timeout_covers_response_stream_and_closes_generator() -> None:
    app = App()
    app.use(timeout(0.01))
    generator_closed = False

    async def stream() -> AsyncGenerator[bytes, None]:
        nonlocal generator_closed
        try:
            yield b"first"
            await asyncio.Event().wait()
            yield b"second"
        finally:
            generator_closed = True

    async def stream_handler(ctx: Context) -> StreamResponse:
        return StreamResponse(stream())

    app.add_route("GET", "/stream", stream_handler)

    sent_messages: list[HTTPSendEvent] = []
    await app(
        http_scope(path="/stream"),
        http_receive([{"type": "http.request", "body": b""}]),
        http_sender(sent_messages),
    )

    assert sent_messages[0]["type"] == "http.response.start"
    assert sent_messages[0]["status"] == 200
    assert sent_messages[1]["type"] == "http.response.body"
    assert sent_messages[1]["body"] == b"first"
    assert sent_messages[-1] == {
        "type": "http.response.body",
        "body": b"",
        "more_body": False,
    }
    assert generator_closed


@pytest.mark.asyncio
async def test_nested_timeouts_use_the_earliest_deadline() -> None:
    app = App()
    app.use(timeout(0), timeout(60))

    async def slow_handler(ctx: Context) -> str:
        await asyncio.Event().wait()
        return "done"

    app.add_route("GET", "/slow", slow_handler)

    sent_messages: list[HTTPSendEvent] = []
    await app(
        http_scope(path="/slow"),
        http_receive([{"type": "http.request", "body": b""}]),
        http_sender(sent_messages),
    )

    assert sent_messages[0]["type"] == "http.response.start"
    assert sent_messages[0]["status"] == 504


@given(count=st.integers(0, 8))
async def test_generated_middleware_chain_preserves_nesting(count: int) -> None:
    events: list[tuple[str, int]] = []

    def middleware(index: int):
        def wrap(next_handler: Handler) -> Handler:
            async def handler(ctx: Context) -> Response:
                events.append(("enter", index))
                response = await next_handler(ctx)
                events.append(("exit", index))
                return response

            return handler

        return wrap

    async def target(ctx: Context) -> Response:
        return TextResponse("ok")

    await build_chain(target, [middleware(i) for i in range(count)])(http_context())
    assert events == [("enter", i) for i in range(count)] + [
        ("exit", i) for i in reversed(range(count))
    ]


@given(
    location=st.sampled_from(["pre", "global", "route", "handler"]),
    status=st.sampled_from([400, 413, 429, 500]),
    sync=st.booleans(),
)
async def test_errors_preserve_outer_middleware_and_render_once(
    location: str,
    status: int,
    sync: bool,
) -> None:
    app = App()
    rendered: list[Exception] = []

    def fail() -> None:
        if status == 500:
            raise ValueError("failure")
        raise HTTPException(status, "failure")

    def faulty(next_handler: Handler) -> Handler:
        async def handler(ctx: Context) -> Response:
            fail()
            return await next_handler(ctx)

        return handler

    def error_handler(ctx: Context, exc: Exception) -> Response:
        rendered.append(exc)
        return TextResponse("handled", status_code=status)

    async def async_error_handler(ctx: Context, exc: Exception) -> Response:
        return error_handler(ctx, exc)

    app.set_error_handler(error_handler if sync else async_error_handler)
    app.use_pre(
        cors(allow_origins=["https://example.com"]),
        request_id(generator=lambda: "test-id"),
        recover(),
    )
    if location == "pre":
        app.use_pre(faulty)
    elif location == "global":
        app.use(faulty)

    async def target(ctx: Context) -> str:
        if location == "handler":
            fail()
        return "ok"

    app.add_route(
        "GET", "/", target, middlewares=[faulty] if location == "route" else []
    )
    sent: list[HTTPSendEvent] = []
    with patch.object(logging.getLogger("lettia.app"), "exception") as log:
        await app(
            http_scope(headers=[(b"origin", b"https://example.com")]),
            http_receive([]),
            http_sender(sent),
        )
        assert log.call_count == (1 if status == 500 else 0)
    assert len(rendered) == 1
    start = sent[0]
    assert start["type"] == "http.response.start" and start["status"] == status
    headers = dict(start["headers"])
    assert headers[b"access-control-allow-origin"] == b"https://example.com"
    assert headers[b"x-request-id"] == b"test-id"


@given(limit=st.integers(0, 32))
async def test_route_limit_cannot_be_bypassed_by_global_body_cache(limit: int) -> None:
    app = App()
    app.use(body_limit(100))
    app.add_route("POST", "/", lambda ctx: "accepted", middlewares=[body_limit(limit)])
    sent: list[HTTPSendEvent] = []
    await app(
        http_scope(method="POST"),
        http_receive([{"type": "http.request", "body": b"x" * (limit + 1)}]),
        http_sender(sent),
    )
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413


@given(seconds=st.integers(1, 100))
async def test_timeout_middleware_does_not_relabel_upstream_timeout(
    seconds: int,
) -> None:
    async def fail(ctx: Context) -> Response:
        raise TimeoutError("upstream")

    with pytest.raises(TimeoutError, match="upstream"):
        await timeout(seconds)(fail)(http_context())


@given(
    tokens=st.lists(
        st.sampled_from(["Accept-Encoding", "User-Agent", "origin", "ORIGIN", "*"]),
        max_size=8,
    ),
    uppercase=st.booleans(),
)
@example(tokens=["Accept-Encoding"], uppercase=False)
async def test_cors_merges_vary_without_losing_existing_tokens(
    tokens: list[str], uppercase: bool
) -> None:
    async def target(ctx: Context) -> Response:
        headers = {"vary": ", ".join(tokens)}
        if uppercase:
            midpoint = len(tokens) // 2
            headers = {
                "Vary": ", ".join(tokens[:midpoint]),
                "vary": ", ".join(tokens[midpoint:]),
            }
        return Response(headers=headers)

    ctx = http_context(headers=[(b"origin", b"https://example.com")])
    chain = cors(allow_origins=["https://example.com"])
    response = await chain(chain(target))(ctx)
    values = [value for key, value in response.headers.items() if key.lower() == "vary"]
    assert len(values) == 1
    actual = [token.strip().lower() for token in values[0].split(",")]
    assert len(actual) == len(set(actual))
    expected = {token.lower() for token in tokens}
    assert set(actual) == ({"*"} if "*" in expected else expected | {"origin"})
