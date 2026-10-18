from collections.abc import Callable
from typing import Annotated, Any, Protocol

import msgspec

from lettia import (
    Body,
    Context,
    Cookie,
    Created,
    File,
    Form,
    GuardSpec,
    Header,
    HeaderName,
    NoContent,
    NotFound,
    Ok,
    Path,
    Problem,
    Query,
    UploadFile,
    op,
    public,
)
from lettia.contract.introspect import Operation, RequestField, inspect_contract


class User(msgspec.Struct):
    uid: int


class NewUser(msgspec.Struct):
    name: str


class UserOut(msgspec.Struct):
    id: int


class GetUserReq(msgspec.Struct):
    user_id: Path[int]
    expand: Query[bool] = False
    tags: Query[list[int]] = msgspec.field(default_factory=list)
    user_agent: Header[str] = ""
    token: Header[Annotated[str, HeaderName("X-Token")]] = ""
    session: Cookie[str] = ""


class CreateUserReq(msgspec.Struct):
    body: Body[NewUser]


class UserCreated(Created[UserOut], kw_only=True):
    location: Annotated[str, HeaderName("Location")]


class Text(Ok[str]):
    media_type = "text/plain"


bearer = GuardSpec[User]("bearer")


class UserApi(Protocol):
    @op.get("/users/{user_id}", guard=bearer, tags=["users"])
    async def get_user(
        self, c: Context[User], req: GetUserReq
    ) -> Ok[UserOut] | NotFound[Problem]:
        """Get a single user."""
        ...

    @op.post("/users", guard=bearer, operation_id="createUser")
    async def create_user(
        self, c: Context[User], req: CreateUserReq
    ) -> UserCreated: ...

    @op.get("/healthz", guard=public)
    async def health(self, c: Context[None], req: None) -> NoContent: ...

    @op.get("/motd", guard=public)
    async def motd(self, c: Context[None], req: None) -> Text: ...

    async def not_an_operation(self) -> None: ...


def by_name(operations: list[Operation]) -> dict[str, Operation]:
    return {o.name: o for o in operations}


# --- well-formed contracts ----------------------------------------------


def test_inspect_collects_every_decorated_method_and_ignores_the_rest() -> None:
    operations, problems = inspect_contract(UserApi)

    assert problems == []
    assert sorted(by_name(operations)) == ["create_user", "get_user", "health", "motd"]


def test_inspect_operation_id_defaults_to_the_method_name_and_can_be_overridden() -> (
    None
):
    operations = by_name(inspect_contract(UserApi)[0])

    assert operations["get_user"].operation_id == "get_user"
    assert operations["create_user"].operation_id == "createUser"


def test_inspect_keeps_the_spec_and_the_docstring() -> None:
    get_user = by_name(inspect_contract(UserApi)[0])["get_user"]

    assert get_user.spec.path == "/users/{user_id}"
    assert get_user.spec.tags == ("users",)
    assert get_user.doc == "Get a single user."
    assert by_name(inspect_contract(UserApi)[0])["health"].doc is None
    assert get_user.contract is UserApi


def test_inspect_response_headers_know_their_type_and_whether_they_may_be_absent() -> (
    None
):
    class WithOptional(Ok[int]):
        etag: Annotated[str | None, HeaderName("ETag")] = None
        retries: Annotated[int, HeaderName("X-Retries")] = 0

    api = protocol(
        optional=op.get("/o", guard=public)(annotated_returning(WithOptional))
    )

    [operation], problems = inspect_contract(api)

    assert problems == []
    etag, retries = operation.responses[0].headers
    assert (etag.wire_name, etag.type, etag.required) == ("ETag", str, False)
    assert (retries.wire_name, retries.type, retries.required) == (
        "X-Retries",
        int,
        True,
    )


def test_inspect_request_fields_know_their_default_and_nullability() -> None:
    fields = {
        f.name: f for f in by_name(inspect_contract(UserApi)[0])["get_user"].fields
    }
    body = by_name(inspect_contract(UserApi)[0])["create_user"].fields[0]

    assert fields["user_id"].default is msgspec.NODEFAULT
    assert fields["expand"].default is False
    assert fields["tags"].default is msgspec.NODEFAULT  # a factory, not a value
    assert not fields["expand"].nullable
    assert not body.nullable


def test_inspect_marks_a_none_accepting_field_as_nullable() -> None:
    class Optional(msgspec.Struct):
        body: Body[NewUser | None] = None

    api = protocol(optional=op.post("/o", guard=public)(annotated(Optional, Ok[int])))

    [operation], problems = inspect_contract(api)

    assert problems == []
    assert operation.fields[0].nullable


def test_inspect_describes_request_fields_by_source() -> None:
    fields = {
        f.name: f for f in by_name(inspect_contract(UserApi)[0])["get_user"].fields
    }

    assert fields["user_id"] == RequestField(
        "user_id", "path", int, "user_id", True, False
    )
    assert fields["expand"] == RequestField(
        "expand", "query", bool, "expand", False, False, default=False
    )
    assert fields["tags"] == RequestField(
        "tags", "query", list[int], "tags", False, True
    )
    assert fields["session"].source == "cookie"
    assert fields["session"].wire_name == "session"


def test_inspect_derives_header_names_from_fields_unless_named() -> None:
    fields = {
        f.name: f for f in by_name(inspect_contract(UserApi)[0])["get_user"].fields
    }

    assert fields["user_agent"].source == "header"
    assert fields["user_agent"].wire_name == "user-agent"
    assert fields["token"].wire_name == "X-Token"
    assert fields["token"].type is str


def test_inspect_body_field() -> None:
    create = by_name(inspect_contract(UserApi)[0])["create_user"]

    assert create.request_type is CreateUserReq
    assert [(f.name, f.source, f.type, f.required) for f in create.fields] == [
        ("body", "body", NewUser, True)
    ]


def test_inspect_none_request_has_no_type_and_no_fields() -> None:
    health = by_name(inspect_contract(UserApi)[0])["health"]

    assert health.request_type is None
    assert health.fields == ()


def test_inspect_response_union_lists_status_and_body_per_member() -> None:
    get_user = by_name(inspect_contract(UserApi)[0])["get_user"]

    assert [(r.cls, r.status, r.body_type) for r in get_user.responses] == [
        (Ok, 200, UserOut),
        (NotFound, 404, Problem),
    ]
    assert all(r.media_type == "application/json" for r in get_user.responses)


def test_inspect_response_without_a_body() -> None:
    [response] = by_name(inspect_contract(UserApi)[0])["health"].responses

    assert (response.status, response.body_type, response.headers) == (204, None, ())


def test_inspect_response_subclass_with_headers_and_a_resolved_body_type() -> None:
    [response] = by_name(inspect_contract(UserApi)[0])["create_user"].responses

    assert response.cls is UserCreated
    assert response.status == 201
    assert response.body_type is UserOut
    assert [
        (h.attribute, h.wire_name, h.type, h.required) for h in response.headers
    ] == [("location", "Location", str, True)]


def test_inspect_response_media_type_comes_from_the_class() -> None:
    [response] = by_name(inspect_contract(UserApi)[0])["motd"].responses

    assert response.media_type == "text/plain"
    assert response.body_type is str


# --- malformed contracts ------------------------------------------------


def protocol(**members: Any) -> type:
    """A ``Protocol`` class built at runtime from plain members."""
    bases: Any = (Protocol,)
    return type("Api", bases, members)


def contract_with(
    function: Callable[..., Any], *, path: str = "/x", method: str = "get"
) -> type:
    """A one-operation contract built without static checking of its annotations."""
    declare: Any = getattr(op, method)
    function.__qualname__ = function.__name__ = "broken"
    return protocol(broken=declare(path, guard=public)(function))


def problems_of(function: Callable[..., Any], **kwargs: str) -> list[str]:
    operations, problems = inspect_contract(contract_with(function, **kwargs))
    assert operations == []
    return problems


class OnlyPath(msgspec.Struct):
    id: Path[int]


class Unmarked(msgspec.Struct):
    id: int


class WrappedBody(msgspec.Struct):
    body: Body[NewUser] | None = None


class TwoBodies(msgspec.Struct):
    first: Body[NewUser]
    second: Body[NewUser]


class Plain(msgspec.Struct):
    q: Query[str] = ""


class Stray(Ok[int]):
    extra: str = ""


async def ok_handler(self: Any, c: Any, req: Any) -> Ok[int]:
    return Ok(1)


def annotated(req: object, returns: object, name: str = "broken") -> Callable[..., Any]:
    async def function(self: Any, c: Any, req: Any) -> Any: ...

    function.__annotations__ = {"c": Context[None], "req": req, "return": returns}
    return function


def test_inspect_reports_a_path_placeholder_without_a_path_field() -> None:
    [problem] = problems_of(annotated(Plain, Ok[int]), path="/items/{item_id}")

    assert problem.startswith("Api.broken:")
    assert "item_id" in problem


def test_inspect_reports_a_path_field_without_a_placeholder() -> None:
    [problem] = problems_of(annotated(OnlyPath, Ok[int]), path="/items")

    assert "id" in problem
    assert "placeholder" in problem


def test_inspect_reports_a_wildcard_in_a_contract_path() -> None:
    problems = problems_of(annotated(None, Ok[int]), path="/files/*")

    assert any("*" in p for p in problems)


def test_inspect_reports_a_request_field_without_a_source_marker() -> None:
    [problem] = problems_of(annotated(Unmarked, Ok[int]))

    assert "'id'" in problem
    assert "marker" in problem


def test_inspect_hints_at_the_right_way_to_make_a_body_optional() -> None:
    [problem] = problems_of(annotated(WrappedBody, Ok[int]), method="post")

    assert "Body[" in problem
    assert "| None" in problem


def test_inspect_reports_more_than_one_body() -> None:
    [problem] = problems_of(annotated(TwoBodies, Ok[int]), method="post")

    assert "one Body" in problem


def test_inspect_reports_a_body_on_a_get_or_delete() -> None:
    for method in ("get", "delete"):
        [problem] = problems_of(annotated(CreateUserReq, Ok[int]), method=method)
        assert method.upper() in problem


def test_inspect_reports_a_request_type_that_is_not_a_struct_or_none() -> None:
    [problem] = problems_of(annotated(int, Ok[int]))

    assert "Struct" in problem


def test_inspect_reports_duplicate_status_codes_in_the_response_union() -> None:
    [problem] = problems_of(annotated(None, Ok[int] | Ok[str]))

    assert "200" in problem
    assert "once" in problem


def test_inspect_reports_a_response_that_is_not_a_response_class() -> None:
    [problem] = problems_of(annotated(None, int))

    assert "int" in problem
    assert "response class" in problem


def test_inspect_reports_a_response_class_with_a_stray_field() -> None:
    [problem] = problems_of(annotated(None, Stray))

    assert "'extra'" in problem
    assert "HeaderName" in problem


def test_inspect_reports_a_missing_return_annotation() -> None:
    function = annotated(None, None)
    del function.__annotations__["return"]

    [problem] = problems_of(function)

    assert "return" in problem


def test_inspect_reports_a_missing_request_annotation() -> None:
    function = annotated(None, Ok[int])
    del function.__annotations__["req"]

    [problem] = problems_of(function)

    assert "req" in problem


def test_inspect_reports_a_wrong_parameter_list() -> None:
    async def function(self: Any, c: Any) -> Any: ...

    function.__annotations__ = {"return": Ok[int]}

    [problem] = problems_of(function)

    assert "(self, c, req)" in problem


def test_inspect_reports_annotations_that_cannot_be_resolved() -> None:
    function = annotated("MissingName", Ok[int])

    [problem] = problems_of(function)

    assert "MissingName" in problem


def test_inspect_keeps_valid_operations_when_a_sibling_is_malformed() -> None:
    good = op.get("/good", guard=public)(ok_handler_annotated())
    bad = op.get("/bad", guard=public)(annotated(int, Ok[int]))
    api = protocol(good=good, bad=bad)

    operations, problems = inspect_contract(api)

    assert [o.name for o in operations] == ["good"]
    assert len(problems) == 1
    assert problems[0].startswith("Api.bad:")


def annotated_returning(response: object) -> Callable[..., Any]:
    return annotated(None, response)


def ok_handler_annotated() -> Callable[..., Any]:
    async def good(self: Any, c: Any, req: Any) -> Any: ...

    good.__annotations__ = {"c": Context[None], "req": None, "return": Ok[int]}
    return good


class FormAndFileReq(msgspec.Struct, kw_only=True):
    name: Form[str]
    count: Form[int] = 1
    avatar: File[UploadFile]
    docs: File[list[UploadFile]] = msgspec.field(default_factory=list)


def test_inspect_recognizes_form_and_file_markers() -> None:
    class UploadApi(Protocol):
        @op.post("/upload", guard=public)
        async def upload(self, c: Context[None], req: FormAndFileReq) -> Ok[int]: ...

    [operation], problems = inspect_contract(UploadApi)

    assert problems == []
    fields = {f.name: f for f in operation.fields}
    assert fields["name"].source == "form"
    assert fields["name"].type is str
    assert fields["count"].source == "form"
    assert fields["count"].type is int
    assert fields["avatar"].source == "file"
    assert fields["avatar"].type is UploadFile
    assert fields["docs"].source == "file"
    assert fields["docs"].type == list[UploadFile]
    assert fields["docs"].multi is True


def test_inspect_reports_mixing_body_with_form_or_file() -> None:
    class MixedReq(msgspec.Struct):
        body: Body[NewUser]
        extra: Form[str]

    class MixedApi(Protocol):
        @op.post("/mixed", guard=public)
        async def mixed(self, c: Context[None], req: MixedReq) -> Ok[int]: ...

    operations, problems = inspect_contract(MixedApi)

    assert operations == []
    assert any("cannot mix Body fields" in p for p in problems)


def test_inspect_reports_form_or_file_on_methods_without_body() -> None:
    class FormGetReq(msgspec.Struct):
        name: Form[str]

    class GetApi(Protocol):
        @op.get("/items", guard=public)
        async def get_items(self, c: Context[None], req: FormGetReq) -> Ok[int]: ...

    operations, problems = inspect_contract(GetApi)

    assert operations == []
    assert any("GET operations cannot have Form or File fields" in p for p in problems)
