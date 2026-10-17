from collections.abc import AsyncIterator
from typing import Annotated, Protocol

import msgspec
import pytest

from lettia import (
    Body,
    Context,
    Cookie,
    Header,
    HeaderName,
    NoContent,
    Ok,
    Path,
    Query,
    op,
    public,
)
from lettia.contract.codec import MsgspecCodec
from lettia.contract.decode import compile_request
from lettia.contract.introspect import Operation, inspect_contract
from lettia.core import Context as CoreContext
from lettia.core import HTTPError, Request


class NewUser(msgspec.Struct):
    name: str
    age: int = 0


class Everything(msgspec.Struct):
    user_id: Path[int]
    expand: Query[bool] = False
    tags: Query[list[int]] = msgspec.field(default_factory=list)
    first: Query[str] = ""
    agent: Header[str] = ""
    token: Header[Annotated[str, HeaderName("X-Token")]] = ""
    session: Cookie[str] = ""
    body: Body[NewUser | None] = None


class RequiredBody(msgspec.Struct):
    body: Body[NewUser]


class NullableBody(msgspec.Struct):
    body: Body[NewUser | None]


class RequiredQuery(msgspec.Struct):
    page: Query[int]
    token: Header[str]


class Api(Protocol):
    @op.put("/users/{user_id}", guard=public)
    async def everything(self, c: Context[None], req: Everything) -> Ok[int]: ...

    @op.post("/required", guard=public)
    async def required_body(self, c: Context[None], req: RequiredBody) -> NoContent: ...

    @op.post("/nullable", guard=public)
    async def nullable_body(self, c: Context[None], req: NullableBody) -> NoContent: ...

    @op.get("/query", guard=public)
    async def required_query(
        self, c: Context[None], req: RequiredQuery
    ) -> NoContent: ...

    @op.get("/none", guard=public)
    async def nothing(self, c: Context[None], req: None) -> NoContent: ...


def operation(name: str) -> Operation:
    operations, problems = inspect_contract(Api)
    assert problems == []
    return next(o for o in operations if o.name == name)


class Unread(Exception):
    pass


def context(
    *,
    path_params: dict[str, str] | None = None,
    query: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
    body: bytes | None = b"",
) -> CoreContext:
    async def chunks() -> AsyncIterator[bytes]:
        if body is None:
            raise Unread
        if body:
            yield body

    request = Request(
        method="POST",
        path="/",
        query_string=query,
        headers=headers,
        body=chunks(),
    )
    return CoreContext(request, path_params)


def json_headers(
    content_type: bytes = b"application/json",
) -> list[tuple[bytes, bytes]]:
    return [(b"content-type", content_type)]


codec = MsgspecCodec()


async def decode(name: str, c: CoreContext) -> msgspec.Struct | None:
    decoder = compile_request(operation(name), codec)
    return None if decoder is None else await decoder(c)


async def invalid(name: str, c: CoreContext) -> HTTPError:
    with pytest.raises(HTTPError) as caught:
        await decode(name, c)
    return caught.value


# --- successful decoding ------------------------------------------------


async def test_decode_returns_an_instance_of_the_request_type() -> None:
    req = await decode("everything", context(path_params={"user_id": "7"}))

    assert isinstance(req, Everything)


async def test_decode_converts_path_parameters() -> None:
    req = await decode("everything", context(path_params={"user_id": "7"}))

    assert isinstance(req, Everything)
    assert req.user_id == 7


async def test_decode_applies_defaults_for_absent_values() -> None:
    req = await decode("everything", context(path_params={"user_id": "1"}))

    assert isinstance(req, Everything)
    assert (req.expand, req.tags, req.first, req.agent, req.session, req.body) == (
        False,
        [],
        "",
        "",
        "",
        None,
    )


async def test_decode_reads_query_header_and_cookie_values() -> None:
    c = context(
        path_params={"user_id": "1"},
        query=b"expand=true&tags=1&tags=2&first=a&first=b",
        headers=[
            (b"agent", b"curl"),
            (b"x-token", b"secret"),
            (b"cookie", b"session=abc; other=1"),
        ],
    )

    req = await decode("everything", c)

    assert isinstance(req, Everything)
    assert req.expand is True
    assert req.tags == [1, 2]
    assert req.first == "a"  # a scalar field takes the first value
    assert (req.agent, req.token, req.session) == ("curl", "secret", "abc")


async def test_decode_header_lookup_ignores_case_and_derives_dashes() -> None:
    c = context(path_params={"user_id": "1"}, headers=[(b"x-token", b"t")])

    req = await decode("everything", c)

    assert isinstance(req, Everything)
    assert req.token == "t"


async def test_decode_ignores_unknown_query_parameters() -> None:
    c = context(path_params={"user_id": "1"}, query=b"unknown=1")

    assert await decode("everything", c) is not None


async def test_decode_reads_a_json_body_strictly() -> None:
    c = context(
        path_params={"user_id": "1"},
        headers=json_headers(),
        body=b'{"name": "Ann", "age": 3}',
    )

    req = await decode("everything", c)

    assert isinstance(req, Everything)
    assert req.body == NewUser("Ann", 3)


@pytest.mark.parametrize(
    "content_type",
    [
        b"application/json",
        b"Application/JSON; charset=utf-8",
        b"application/vnd.api+json",
    ],
)
async def test_decode_accepts_json_media_types(content_type: bytes) -> None:
    c = context(headers=json_headers(content_type), body=b'{"name": "Ann"}')

    req = await decode("required_body", c)

    assert isinstance(req, RequiredBody)
    assert req.body.name == "Ann"


async def test_decode_accepts_a_body_without_a_content_type() -> None:
    req = await decode("required_body", context(body=b'{"name": "Ann"}'))

    assert isinstance(req, RequiredBody)


async def test_decode_empty_body_uses_the_default_or_none_when_allowed() -> None:
    with_default = await decode("everything", context(path_params={"user_id": "1"}))
    nullable = await decode("nullable_body", context())

    assert isinstance(with_default, Everything)
    assert with_default.body is None
    assert isinstance(nullable, NullableBody)
    assert nullable.body is None


async def test_decode_none_request_reads_nothing() -> None:
    decoder = compile_request(operation("nothing"), codec)

    assert decoder is None


async def test_decode_without_a_body_field_never_reads_the_body() -> None:
    c = context(query=b"page=1", headers=[(b"token", b"t")], body=None)

    assert await decode("required_query", c) is not None  # reading would raise Unread


# --- invalid requests ---------------------------------------------------


def errors_of(error: HTTPError) -> list[dict[str, str]]:
    found = error.extensions["errors"]
    assert isinstance(found, list)
    return found


async def test_decode_invalid_path_parameter_is_a_422_with_its_location() -> None:
    error = await invalid("everything", context(path_params={"user_id": "abc"}))

    assert error.status == 422
    assert error.title == "Unprocessable Entity"
    [problem] = errors_of(error)
    assert (problem["in"], problem["path"]) == ("path", "$.path.user_id")
    assert "int" in problem["message"]
    assert error.detail == f"$.path.user_id: {problem['message']}"


async def test_decode_invalid_query_value_names_the_query_source() -> None:
    c = context(path_params={"user_id": "1"}, query=b"expand=maybe")

    [problem] = errors_of(await invalid("everything", c))

    assert (problem["in"], problem["path"]) == ("query", "$.query.expand")


async def test_decode_invalid_list_element_keeps_the_index() -> None:
    c = context(path_params={"user_id": "1"}, query=b"tags=1&tags=x")

    [problem] = errors_of(await invalid("everything", c))

    assert problem["path"] == "$.query.tags[1]"


async def test_decode_missing_required_query_and_header_are_422s() -> None:
    missing_page = await invalid("required_query", context(headers=[(b"token", b"t")]))
    missing_token = await invalid("required_query", context(query=b"page=1"))

    assert errors_of(missing_page)[0]["path"] == "$.query.page"
    assert errors_of(missing_token)[0]["path"] == "$.header.token"


async def test_decode_invalid_body_field_is_located_under_body() -> None:
    c = context(headers=json_headers(), body=b'{"name": 5}')

    [problem] = errors_of(await invalid("required_body", c))

    assert (problem["in"], problem["path"]) == ("body", "$.body.name")


async def test_decode_missing_body_field_is_located_under_body() -> None:
    c = context(headers=json_headers(), body=b"{}")

    [problem] = errors_of(await invalid("required_body", c))

    assert problem["path"] == "$.body.name"


async def test_decode_malformed_json_is_a_422_at_the_body() -> None:
    c = context(headers=json_headers(), body=b"{bad")

    error = await invalid("required_body", c)

    assert error.status == 422
    assert errors_of(error)[0]["path"] == "$.body"


async def test_decode_missing_required_body_is_a_422() -> None:
    error = await invalid("required_body", context())

    [problem] = errors_of(error)
    assert problem["path"] == "$.body"
    assert "required" in problem["message"]


async def test_decode_non_json_content_type_is_a_415() -> None:
    c = context(headers=json_headers(b"text/plain"), body=b'{"name": "Ann"}')

    error = await invalid("required_body", c)

    assert error.status == 415
    assert error.title == "Unsupported Media Type"


async def test_decode_oversized_body_keeps_its_413() -> None:
    async def chunks() -> AsyncIterator[bytes]:
        yield b"x" * 20

    request = Request(
        method="POST",
        path="/",
        headers=json_headers(),
        body=chunks(),
        max_body_size=10,
    )

    error = await invalid("required_body", CoreContext(request))

    assert error.status == 413
