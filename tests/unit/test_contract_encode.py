from collections.abc import AsyncIterator, Callable
from typing import Annotated, Any, Protocol, override

import msgspec
import pytest

from lettia import (
    Context,
    Created,
    HeaderName,
    NoContent,
    NotFound,
    Ok,
    Problem,
    Reply,
    op,
    public,
)
from lettia.contract.codec import MsgspecCodec
from lettia.contract.encode import compile_responses
from lettia.contract.introspect import Operation, inspect_contract
from lettia.extensions.sse import EventStream, ServerSentEvent


class UserOut(msgspec.Struct):
    id: int


class UserCreated(Created[UserOut], kw_only=True):
    location: Annotated[str, HeaderName("Location")]
    note: Annotated[str | None, HeaderName("X-Note")] = None
    count: Annotated[int, HeaderName("X-Count")] = 0


class Text(Ok[str]):
    media_type = "text/plain; charset=utf-8"


class Blob(Ok[bytes]):
    media_type = "application/octet-stream"


class Html(Ok[str]):
    media_type = "text/html"


class Api(Protocol):
    @op.get("/user", guard=public)
    async def user(
        self, c: Context[None], req: None
    ) -> Ok[UserOut] | NotFound[Problem]: ...

    @op.post("/user", guard=public)
    async def create(self, c: Context[None], req: None) -> UserCreated: ...

    @op.delete("/user", guard=public)
    async def delete(self, c: Context[None], req: None) -> NoContent: ...

    @op.get("/text", guard=public)
    async def text(self, c: Context[None], req: None) -> Text: ...

    @op.get("/blob", guard=public)
    async def blob(self, c: Context[None], req: None) -> Blob: ...

    @op.get("/html", guard=public)
    async def html(self, c: Context[None], req: None) -> Html: ...

    @op.get("/nothing", guard=public)
    async def nothing(self, c: Context[None], req: None) -> Ok[None]: ...


def encoder(name: str, *, debug: bool = False) -> Any:
    operations, problems = inspect_contract(Api)
    assert problems == []
    operation: Operation = next(o for o in operations if o.name == name)
    return compile_responses(operation, MsgspecCodec(), debug=debug)


# --- declared responses -------------------------------------------------


def test_encode_ok_sets_status_json_body_and_content_type() -> None:
    res = encoder("user")(Ok(UserOut(7)))

    assert res.status == 200
    assert res.body == b'{"id":7}'
    assert res.headers["content-type"] == "application/json"


def test_encode_each_member_of_the_union_uses_its_own_status() -> None:
    res = encoder("user")(NotFound(Problem(title="no such user")))

    assert res.status == 404
    assert msgspec.json.decode(res.body)["title"] == "no such user"


def test_encode_no_content_has_no_body_and_no_content_type() -> None:
    res = encoder("delete")(NoContent())

    assert (res.status, res.body) == (204, None)
    assert "content-type" not in res.headers


def test_encode_response_headers_come_from_the_header_fields() -> None:
    res = encoder("create")(UserCreated(UserOut(1), location="/users/1", count=3))

    assert res.status == 201
    assert res.headers["location"] == "/users/1"
    assert res.headers["x-count"] == "3"
    assert res.body == b'{"id":1}'


def test_encode_skips_header_fields_that_are_none() -> None:
    res = encoder("create")(UserCreated(UserOut(1), location="/users/1"))

    assert "x-note" not in res.headers


def test_encode_rejects_a_header_value_with_a_line_break() -> None:
    reply = UserCreated(UserOut(1), location="/users/1\r\nX-Evil: 1")

    with pytest.raises(ValueError, match="line break"):
        encoder("create")(reply)


def test_encode_text_media_type_writes_a_str_body_as_utf8() -> None:
    res = encoder("text")(Text("héllo"))

    assert res.body == "héllo".encode()
    assert res.headers["content-type"] == "text/plain; charset=utf-8"


def test_encode_non_json_media_type_passes_bytes_through() -> None:
    res = encoder("blob")(Blob(b"\x00\x01"))

    assert res.body == b"\x00\x01"


def test_encode_non_json_media_type_rejects_a_body_that_is_not_text_or_bytes() -> None:
    with pytest.raises(TypeError, match="text/plain"):
        encoder("text")(Text(5))  # type: ignore[arg-type]


def test_encode_a_none_body_is_json_null() -> None:
    res = encoder("nothing")(Ok(None))

    assert res.body == b"null"


# --- undeclared responses -----------------------------------------------


def test_encode_rejects_a_response_class_the_contract_does_not_declare() -> None:
    with pytest.raises(RuntimeError, match="NotFound"):
        encoder("create")(NotFound(Problem(title="x")))


def test_encode_accepts_a_subclass_of_a_declared_response_class() -> None:
    class Special(UserCreated):
        pass

    res = encoder("create")(Special(UserOut(1), location="/x"))

    assert res.status == 201
    assert res.headers["location"] == "/x"


def test_encode_rejects_something_that_is_not_a_response_at_all() -> None:
    reply: Reply = Reply()

    with pytest.raises(RuntimeError, match="Reply"):
        encoder("delete")(reply)


# --- debug check of the body ---------------------------------------------


class Stranger(msgspec.Struct):
    label: str


def test_encode_without_debug_does_not_check_the_body_against_the_declaration() -> None:
    res = encoder("user")(Ok(Stranger("x")))  # type: ignore[arg-type]

    assert res.body == b'{"label":"x"}'


def test_encode_in_debug_rejects_a_body_that_does_not_match_the_declaration() -> None:
    with pytest.raises(RuntimeError) as caught:
        encoder("user", debug=True)(Ok(Stranger("x")))  # type: ignore[arg-type]

    assert "Api.user" in str(caught.value)
    assert "does not match" in str(caught.value)
    assert "UserOut" in str(caught.value)


def test_encode_in_debug_names_the_offending_field() -> None:
    with pytest.raises(RuntimeError, match=r"\$\.id"):
        encoder("user", debug=True)(Ok({"id": "x"}))  # type: ignore[arg-type]


def test_encode_in_debug_accepts_what_the_client_would_see_as_valid() -> None:
    res = encoder("user", debug=True)(Ok(UserOut(7)))
    as_dict = encoder("user", debug=True)(Ok({"id": 7}))  # type: ignore[arg-type]

    assert res.body == as_dict.body == b'{"id":7}'


def test_encode_in_debug_leaves_non_json_bodies_alone() -> None:
    assert encoder("text", debug=True)(Text("plain")).body == b"plain"


def test_encode_in_debug_still_accepts_responses_without_a_body() -> None:
    assert encoder("delete", debug=True)(NoContent()).status == 204


def test_encode_rejects_a_required_header_that_is_none() -> None:
    reply = UserCreated(UserOut(1), location=None)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError) as caught:
        encoder("create")(reply)

    assert "Api.create" in str(caught.value)
    assert "location" in str(caught.value)


def test_encode_text_media_type_without_charset_gets_utf8() -> None:
    res = encoder("html")(Html("<p>hé</p>"))

    assert res.headers["content-type"] == "text/html; charset=utf-8"
    assert res.body == "<p>hé</p>".encode()


def test_encode_keeps_a_charset_the_class_declares() -> None:
    assert encoder("text")(Text("x")).headers["content-type"] == (
        "text/plain; charset=utf-8"
    )


def test_encode_does_not_add_a_charset_to_binary_media_types() -> None:
    assert encoder("blob")(Blob(b"x")).headers["content-type"] == (
        "application/octet-stream"
    )


# --- stream responses ---------------------------------------------------


class Tick(msgspec.Struct):
    n: int


class StreamApi(Protocol):
    @op.get("/ticks", guard=public)
    async def ticks(self, c: Context[None], req: None) -> EventStream[Tick]: ...


class SlowTicks[T](EventStream[T]):
    heartbeat_sec = 0.01
    media_type = "text/plain"
    default_headers = (("x-other", "1"),)

    @override
    def frames(
        self,
        encode: Callable[[object], bytes],
        *,
        check: Callable[[object], None] | None = None,
    ) -> AsyncIterator[bytes]:
        raise AssertionError("an undeclared subclass must not replace the frames")


def stream_encoder(*, debug: bool = False) -> Any:
    operations, problems = inspect_contract(StreamApi)
    assert problems == []
    return compile_responses(operations[0], MsgspecCodec(), debug=debug)


class Source:
    def __init__(self, *items: object) -> None:
        self.items = list(items)
        self.reads = 0

    def __aiter__(self) -> "Source":
        return self

    async def __anext__(self) -> ServerSentEvent[Tick]:
        self.reads += 1
        if not self.items:
            raise StopAsyncIteration
        return self.items.pop(0)  # type: ignore[return-value]


async def drain(body: Any) -> list[bytes]:
    return [frame async for frame in body]


async def test_encode_stream_reply_builds_a_watched_streaming_response() -> None:
    source = Source(ServerSentEvent(Tick(1), event="tick"))

    res = stream_encoder()(EventStream[Tick](source))

    assert source.reads == 0  # nothing is consumed or serialized up front
    assert res.status == 200
    assert res.watch_disconnect is True
    assert res.headers["content-type"] == "text/event-stream"
    assert res.headers["cache-control"] == "no-cache"
    assert res.headers["x-accel-buffering"] == "no"
    assert await drain(res.body) == [
        b": connected\n\n",
        b'event: tick\ndata: {"n":1}\n\n',
    ]


async def test_encode_undeclared_stream_subclass_cannot_change_the_wire_plan() -> None:
    res = stream_encoder()(SlowTicks[Tick](Source(ServerSentEvent(Tick(1)))))

    assert res.headers["content-type"] == "text/event-stream"
    assert "x-other" not in res.headers
    assert (await drain(res.body))[-1] == b'data: {"n":1}\n\n'


async def test_encode_debug_stream_ends_on_an_item_that_breaks_the_declared_type() -> (
    None
):
    bad = ServerSentEvent(Tick("x"))  # type: ignore[arg-type]
    res = stream_encoder(debug=True)(EventStream[Tick](Source(bad)))
    frames = aiter(res.body)
    assert await anext(frames) == b": connected\n\n"

    with pytest.raises(RuntimeError, match=r"StreamApi\.ticks.*declared"):
        await anext(frames)

    with pytest.raises(StopAsyncIteration):
        await anext(frames)


async def test_encode_normal_mode_does_not_check_stream_items() -> None:
    bad = ServerSentEvent(Tick("x"))  # type: ignore[arg-type]
    res = stream_encoder()(EventStream[Tick](Source(bad)))

    assert (await drain(res.body))[-1] == b'data: {"n":"x"}\n\n'
