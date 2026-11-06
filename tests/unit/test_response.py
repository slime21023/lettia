from collections.abc import AsyncIterator
from datetime import UTC, datetime

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


def test_response_set_cookie_adds_a_header_and_keeps_the_others() -> None:
    res = Response()

    res.set_cookie("a", "1")
    res.set_cookie("b", "2", secure=True, max_age=5)

    assert res.headers.get_all("set-cookie") == [
        "a=1; Path=/; HttpOnly; SameSite=Lax",
        "b=2; Max-Age=5; Path=/; Secure; HttpOnly; SameSite=Lax",
    ]


def test_response_set_cookie_formats_an_aware_expiry_as_a_gmt_date() -> None:
    res = Response()

    res.set_cookie("a", "1", expires=datetime(2030, 1, 1, tzinfo=UTC))

    assert "Expires=Tue, 01 Jan 2030 00:00:00 GMT" in res.headers["set-cookie"]


def test_response_set_cookie_rejects_a_naive_expiry() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        Response().set_cookie("a", "1", expires=datetime(2030, 1, 1))  # noqa: DTZ001


def test_response_set_cookie_adds_nothing_when_a_part_is_invalid() -> None:
    res = Response()

    with pytest.raises(ValueError):
        res.set_cookie("a", "x;y")

    assert res.headers.get_all("set-cookie") == []


def test_response_delete_cookie_expires_it_with_the_same_path_and_domain() -> None:
    res = Response()

    res.delete_cookie("sid", path="/app", domain="example.com", secure=True)

    assert res.headers["set-cookie"] == (
        "sid=; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Max-Age=0; "
        "Domain=example.com; Path=/app; Secure; HttpOnly; SameSite=Lax"
    )
