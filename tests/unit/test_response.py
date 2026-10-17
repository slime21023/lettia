from collections.abc import AsyncIterator

import pytest

from lettia.core import MutableHeaders, Response


async def chunks() -> AsyncIterator[bytes]:
    yield b"a"


def test_response_defaults_to_an_empty_200() -> None:
    res = Response()

    assert (res.status, res.body, len(res.headers)) == (200, None, 0)


def test_response_holds_status_bytes_body_and_headers() -> None:
    headers = MutableHeaders()
    headers.set("content-type", "text/plain")

    res = Response(201, b"hi", headers)

    assert (res.status, res.body) == (201, b"hi")
    assert res.headers["content-type"] == "text/plain"


def test_response_accepts_a_streaming_body() -> None:
    body = chunks()

    assert Response(200, body).body is body


def test_response_headers_are_mutable_after_creation() -> None:
    res = Response()

    res.headers.add("x-request-id", "abc")

    assert res.headers["x-request-id"] == "abc"


def test_response_instances_do_not_share_default_headers() -> None:
    first, second = Response(), Response()

    first.headers.set("x-a", "1")

    assert "x-a" not in second.headers


@pytest.mark.parametrize("status", [100, 200, 204, 304, 404, 599])
def test_response_accepts_every_valid_status(status: int) -> None:
    assert Response(status).status == status


@pytest.mark.parametrize("status", [0, 99, 600, -1])
def test_response_rejects_status_outside_100_to_599(status: int) -> None:
    with pytest.raises(ValueError, match="status"):
        Response(status)
