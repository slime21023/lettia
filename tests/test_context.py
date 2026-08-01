from typing import Any

import pytest

from lettia.context import Context
from lettia.errors import HTTPException


def test_context_lazy_query_and_headers() -> None:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/test",
        "query_string": b"name=bard&tag=fast&tag=python",
        "headers": [
            (b"host", b"localhost"),
            (b"user-agent", b"pytest-agent"),
            (b"cookie", b"session=xyz123; theme=dark"),
        ],
    }

    ctx = Context(scope=scope, receive=None, send=None)

    assert ctx.method == "GET"
    assert ctx.path == "/test"

    # Query params
    assert ctx.query_params == {"name": ["bard"], "tag": ["fast", "python"]}
    assert ctx.query_param("name") == "bard"
    assert ctx.query_param("tag") == "fast"
    assert ctx.query_param("missing", default="default") == "default"

    # Headers
    assert ctx.header("host") == "localhost"
    assert ctx.header("User-Agent") == "pytest-agent"
    assert ctx.header("missing") is None

    # Cookies
    assert ctx.cookies == {"session": "xyz123", "theme": "dark"}
    assert ctx.cookie("session") == "xyz123"
    assert ctx.cookie("theme") == "dark"
    assert ctx.cookie("nonexistent") is None


@pytest.mark.asyncio
async def test_context_lazy_body_and_json() -> None:
    async def receive() -> dict[str, Any]:
        return {
            "type": "http.request",
            "body": b'{"key": "value"}',
            "more_body": False,
        }

    scope = {"type": "http", "method": "POST", "path": "/api"}
    ctx = Context(scope=scope, receive=receive, send=None)

    body_bytes = await ctx.body()
    assert body_bytes == b'{"key": "value"}'

    # Second call uses cached body
    body_cached = await ctx.body()
    assert body_cached is body_bytes

    json_data = await ctx.json()
    assert json_data == {"key": "value"}

    text_data = await ctx.text()
    assert text_data == '{"key": "value"}'


def test_context_abort() -> None:
    scope = {"type": "http", "method": "GET", "path": "/abort"}
    ctx = Context(scope=scope, receive=None, send=None)

    with pytest.raises(HTTPException) as exc_info:
        ctx.abort(403, "Access Denied")

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Access Denied"
