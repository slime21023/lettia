import pytest
from asgi_helpers import http_receive, http_scope, http_sender

from lettia.asgi import HTTPSendEvent
from lettia.context import Context
from lettia.errors import HTTPException


def context_for(
    *,
    method: str = "GET",
    path: str = "/",
    query_string: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> Context:
    sent: list[HTTPSendEvent] = []
    return Context(
        scope=http_scope(
            method=method,
            path=path,
            query_string=query_string,
            headers=headers,
        ),
        receive=http_receive([{"type": "http.request", "body": b""}]),
        send=http_sender(sent),
    )


def test_context_lazy_query_and_headers() -> None:
    ctx = context_for(
        path="/test",
        query_string=b"name=bard&tag=fast&tag=python",
        headers=[
            (b"host", b"localhost"),
            (b"user-agent", b"pytest-agent"),
            (b"cookie", b"session=xyz123; theme=dark"),
        ],
    )

    assert ctx.method == "GET"
    assert ctx.path == "/test"
    assert ctx.query_params == {"name": ["bard"], "tag": ["fast", "python"]}
    assert ctx.query_param("name") == "bard"
    assert ctx.query_param("tag") == "fast"
    assert ctx.query_param("missing", default="default") == "default"
    assert ctx.header("host") == "localhost"
    assert ctx.header("User-Agent") == "pytest-agent"
    assert ctx.header("missing") is None
    assert ctx.cookies == {"session": "xyz123", "theme": "dark"}
    assert ctx.cookie("session") == "xyz123"
    assert ctx.cookie("theme") == "dark"
    assert ctx.cookie("nonexistent") is None


def test_context_combines_repeated_cookie_headers_with_cookie_separator() -> None:
    ctx = context_for(headers=[(b"cookie", b"a=1"), (b"cookie", b"b=2")])

    assert ctx.headers["cookie"] == "a=1; b=2"
    assert ctx.cookies == {"a": "1", "b": "2"}


@pytest.mark.asyncio
async def test_context_lazy_body_and_json() -> None:
    sent: list[HTTPSendEvent] = []
    ctx = Context(
        scope=http_scope(method="POST", path="/api"),
        receive=http_receive([{"type": "http.request", "body": b'{"key": "value"}'}]),
        send=http_sender(sent),
    )

    body_bytes = await ctx.body()
    assert body_bytes == b'{"key": "value"}'
    assert await ctx.body() is body_bytes
    assert await ctx.json() == {"key": "value"}
    assert await ctx.text() == '{"key": "value"}'


def test_context_abort() -> None:
    ctx = context_for(path="/abort")
    with pytest.raises(HTTPException) as exc_info:
        ctx.abort(403, "Access Denied")
    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Access Denied"
