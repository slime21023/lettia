import logging
from unittest.mock import patch

import pytest
from hypothesis import given
from hypothesis import strategies as st

from lettia import App
from lettia.asgi import (
    HTTPSendEvent,
)
from lettia.context import Context
from lettia.errors import HTTPException
from lettia.middleware import (
    Handler,
    cors,
    request_id,
)
from lettia.middleware.logger import request_logger
from lettia.middleware.recover import recover
from lettia.response import Response, TextResponse
from lettia.testing import TestClient
from tests.support.asgi import (
    http_receive,
    http_scope,
    http_sender,
)


@pytest.mark.contract("MW-CHAIN", "APP-ERROR")
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


@pytest.mark.contract("MW-CHAIN", "APP-ERROR")
def test_error_response_runs_global_response_middleware() -> None:
    messages: list[str] = []
    app = App()
    app.use(
        cors(allow_origins=["https://example.com"]),
        request_id(generator=lambda: "request-1"),
        request_logger(log_func=messages.append),
    )

    def missing(ctx: Context) -> str:
        ctx.abort(404, "missing")

    app.add_route("GET", "/missing", missing)
    response = TestClient(app).get(
        "/missing", headers={"Origin": "https://example.com"}
    )

    assert response.status_code == 404
    assert response.headers["access-control-allow-origin"] == "https://example.com"
    assert response.headers["x-request-id"] == "request-1"
    assert "GET /missing -> 404" in messages[0]
