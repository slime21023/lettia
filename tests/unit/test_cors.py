import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from lettia.context import Context
from lettia.middleware import cors
from lettia.response import Response, TextResponse
from tests.support.asgi import (
    http_context,
)


@pytest.mark.contract("MW-CORS")
@pytest.mark.asyncio
async def test_cors_middleware() -> None:
    async def handler(ctx: Context) -> Response:
        return TextResponse("OK")

    chain = cors(
        allow_origins=["https://example.com"],
        allow_methods=["GET", "POST"],
        allow_headers=["X-Custom"],
        allow_credentials=True,
    )(handler)
    resp_options = await chain(
        http_context(
            method="OPTIONS",
            path="/api",
            headers=[
                (b"origin", b"https://example.com"),
                (b"access-control-request-method", b"POST"),
            ],
        )
    )
    assert resp_options.status_code == 204
    assert resp_options.headers["access-control-allow-origin"] == "https://example.com"
    assert resp_options.headers["access-control-allow-credentials"] == "true"
    assert "GET, POST" in resp_options.headers["access-control-allow-methods"]

    resp_get = await chain(
        http_context(path="/api", headers=[(b"origin", b"https://example.com")])
    )
    assert resp_get.status_code == 200
    assert resp_get.headers["access-control-allow-origin"] == "https://example.com"


@pytest.mark.contract("MW-CORS")
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


@pytest.mark.contract("MW-CORS")
def test_cors_rejects_wildcard_credentials() -> None:
    with pytest.raises(ValueError, match="wildcard"):
        cors(allow_credentials=True)
