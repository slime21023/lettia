"""Request URL information and allowed_hosts type-check."""

from typing import assert_type

from lettia import App
from lettia.core import Context, RequestHead, Response
from lettia.testing import make_context


def describe(head: RequestHead) -> str:
    assert_type(head.scheme, str)
    assert_type(head.host, str)
    assert_type(head.base_url, str)
    assert_type(head.url, str)
    return f"{head.scheme} {head.host} {head.base_url} {head.url}"


async def handler(c: Context) -> Response:
    return c.text(200, describe(c.request))


def build() -> App:
    app = App(allowed_hosts=["api.example.com", "*.example.com"])
    app.get("/", handler)
    make_context(headers={"host": "api.example.com"}, scheme="https")
    return app
