from typing import Annotated, Any, Protocol

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

    @op.get("/nothing", guard=public)
    async def nothing(self, c: Context[None], req: None) -> Ok[None]: ...


def encoder(name: str) -> Any:
    operations, problems = inspect_contract(Api)
    assert problems == []
    operation: Operation = next(o for o in operations if o.name == name)
    return compile_responses(operation, MsgspecCodec())


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
