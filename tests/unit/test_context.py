from collections.abc import AsyncIterator

import msgspec

from lettia.core import Context, Request


async def no_body() -> AsyncIterator[bytes]:
    return
    yield b""  # pragma: no cover - makes this an async generator


def make_context(path_params: dict[str, str] | None = None) -> Context:
    request = Request(method="GET", path="/", body=no_body())
    return Context(request, path_params)


class User(msgspec.Struct):
    id: int
    name: str


def test_context_exposes_the_request_it_wraps() -> None:
    request = Request(method="GET", path="/x", body=no_body())

    assert Context(request).request is request


def test_context_path_params_default_to_an_empty_mapping() -> None:
    assert make_context().path_params == {}


def test_context_path_params_are_not_shared_between_contexts() -> None:
    first, second = make_context(), make_context()

    first.path_params["id"] = "1"

    assert second.path_params == {}


def test_context_text_sets_status_body_and_utf8_content_type() -> None:
    res = make_context().text(200, "héllo")

    assert res.status == 200
    assert res.body == "héllo".encode()
    assert res.headers["content-type"] == "text/plain; charset=utf-8"


def test_context_json_encodes_structs_with_msgspec() -> None:
    res = make_context().json(201, User(id=1, name="Ann"))

    assert res.status == 201
    assert res.body == b'{"id":1,"name":"Ann"}'
    assert res.headers["content-type"] == "application/json"


def test_context_json_encodes_plain_containers() -> None:
    assert make_context().json(200, {"ok": [1, 2]}).body == b'{"ok":[1,2]}'


def test_context_bytes_uses_the_given_media_type() -> None:
    res = make_context().bytes(200, b"\x00\x01", "application/x-test")

    assert res.body == b"\x00\x01"
    assert res.headers["content-type"] == "application/x-test"


def test_context_bytes_defaults_to_octet_stream() -> None:
    res = make_context().bytes(200, b"x")

    assert res.headers["content-type"] == "application/octet-stream"


def test_context_no_content_is_a_204_without_body_or_content_type() -> None:
    res = make_context().no_content()

    assert (res.status, res.body) == (204, None)
    assert "content-type" not in res.headers


async def test_context_stream_passes_the_chunks_through() -> None:
    async def parts() -> AsyncIterator[bytes]:
        yield b"a"
        yield b"b"

    res = make_context().stream(200, parts(), "text/event-stream")

    assert res.headers["content-type"] == "text/event-stream"
    assert res.body is not None
    assert not isinstance(res.body, bytes)
    assert [part async for part in res.body] == [b"a", b"b"]
