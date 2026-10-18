import json
from typing import Annotated, Any, Protocol

import msgspec
from openapi_spec_validator import validate

from lettia import (
    ApiKeyHeader,
    Body,
    Context,
    Cookie,
    Created,
    GuardSpec,
    Header,
    HeaderName,
    HttpBearer,
    NoContent,
    NotFound,
    Ok,
    Path,
    Problem,
    Query,
    op,
    public,
)
from lettia.contract.codec import MsgspecCodec
from lettia.contract.introspect import inspect_contract
from lettia.contract.mount import MountedOperation
from lettia.contract.openapi import OpenAPIInfo, build_openapi


class User(msgspec.Struct):
    name: str


class ApiKey(msgspec.Struct):
    owner: str


class Admin(msgspec.Struct):
    level: int


class UserOut(msgspec.Struct):
    id: int
    name: str


class NewUser(msgspec.Struct):
    name: str
    age: int = 0


class GetUserReq(msgspec.Struct):
    user_id: Path[int]
    expand: Query[bool] = False
    tags: Query[list[int]] = msgspec.field(default_factory=list)
    agent: Header[str] = ""
    token: Header[Annotated[str, HeaderName("X-Token")]] = ""
    session: Cookie[str] = ""
    limit: Query[int] = 10


class CreateUserReq(msgspec.Struct):
    body: Body[NewUser]


class OptionalBodyReq(msgspec.Struct):
    body: Body[NewUser | None]


class UserCreated(Created[UserOut], kw_only=True):
    location: Annotated[str, HeaderName("Location")]
    etag: Annotated[str | None, HeaderName("ETag")] = None


class Text(Ok[str]):
    media_type = "text/plain; charset=utf-8"


class Blob(Ok[bytes]):
    media_type = "application/octet-stream"


bearer = GuardSpec[User]("bearer", scheme=HttpBearer(format="JWT"))
apikey = GuardSpec[ApiKey]("apikey", scheme=ApiKeyHeader("X-API-Key"))
admin = GuardSpec[Admin]("admin")


class UserApi(Protocol):
    @op.get("/users/{user_id}", guard=bearer, tags=["users"])
    async def get_user(
        self, c: Context[User], req: GetUserReq
    ) -> Ok[UserOut] | NotFound[Problem]:
        """Get a single user.

        Looks the user up by id.
        """
        ...

    @op.post("/users", guard=bearer & admin)
    async def create_user(
        self, c: Context[tuple[User, Admin]], req: CreateUserReq
    ) -> UserCreated:
        """Create a user."""
        ...

    @op.put("/users", guard=public)
    async def upsert(self, c: Context[None], req: OptionalBodyReq) -> NoContent: ...

    @op.get("/healthz", guard=public)
    async def health(self, c: Context[None], req: None) -> NoContent: ...

    @op.get("/either", guard=bearer | apikey)
    async def either(self, c: Context[User | ApiKey], req: None) -> Ok[str]: ...

    @op.get("/maybe", guard=bearer | public)
    async def maybe(self, c: Context[User | None], req: None) -> Ok[str]: ...

    @op.get("/motd", guard=public)
    async def motd(self, c: Context[None], req: None) -> Text: ...

    @op.get("/blob", guard=public)
    async def blob(self, c: Context[None], req: None) -> Blob: ...

    @op.get(
        "/extra", guard=public, openapi_extra={"x-internal": True, "deprecated": True}
    )
    async def extra(self, c: Context[None], req: None) -> NoContent: ...

    @op.get("/only-admin", guard=admin)
    async def only_admin(self, c: Context[Admin], req: None) -> NoContent: ...


INFO = OpenAPIInfo(title="Demo", version="1.2.3", description="A demo API")
codec = MsgspecCodec()


def mounted(contract: type = UserApi, prefix: str = "") -> list[MountedOperation]:
    operations, problems = inspect_contract(contract)
    assert problems == []
    return [MountedOperation(o, prefix + o.spec.path) for o in operations]


def document(
    contract: type = UserApi, prefix: str = "", info: OpenAPIInfo = INFO
) -> dict[str, Any]:
    result, problems = build_openapi(mounted(contract, prefix), info=info, codec=codec)
    assert problems == []
    assert result is not None
    return result


def operation_of(doc: dict[str, Any], method: str, path: str) -> dict[str, Any]:
    return doc["paths"][path][method]


def ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/components/schemas/{name}"}


# --- document shape -----------------------------------------------------


def test_document_header_and_info() -> None:
    doc = document()

    assert doc["openapi"] == "3.1.0"
    assert doc["info"] == {
        "title": "Demo",
        "version": "1.2.3",
        "description": "A demo API",
    }


def test_info_description_is_omitted_when_not_given() -> None:
    doc = document(info=OpenAPIInfo(title="T", version="1"))

    assert doc["info"] == {"title": "T", "version": "1"}


def test_paths_include_the_mount_prefix_and_are_sorted() -> None:
    doc = document(prefix="/v1")

    assert list(doc["paths"]) == sorted(doc["paths"])
    assert "/v1/users/{user_id}" in doc["paths"]
    assert "/users/{user_id}" not in doc["paths"]


def test_methods_of_a_path_come_in_a_fixed_order() -> None:
    doc = document()

    assert list(doc["paths"]["/users"]) == ["put", "post"]


def test_operation_id_tags_summary_and_description() -> None:
    get_user = operation_of(document(), "get", "/users/{user_id}")

    assert get_user["operationId"] == "get_user"
    assert get_user["tags"] == ["users"]
    assert get_user["summary"] == "Get a single user."
    assert get_user["description"] == "Looks the user up by id."


def test_a_one_line_docstring_gives_only_a_summary() -> None:
    create = operation_of(document(), "post", "/users")

    assert create["summary"] == "Create a user."
    assert "description" not in create


def test_an_operation_without_a_docstring_has_neither() -> None:
    health = operation_of(document(), "get", "/healthz")

    assert "summary" not in health
    assert "description" not in health


# --- parameters and request body ----------------------------------------


def test_parameters_are_ordered_by_location_then_name() -> None:
    parameters = operation_of(document(), "get", "/users/{user_id}")["parameters"]

    assert [(p["in"], p["name"]) for p in parameters] == [
        ("path", "user_id"),
        ("query", "expand"),
        ("query", "limit"),
        ("query", "tags"),
        ("header", "X-Token"),
        ("header", "agent"),
        ("cookie", "session"),
    ]


def test_path_parameters_are_required_and_typed() -> None:
    [user_id, *_] = operation_of(document(), "get", "/users/{user_id}")["parameters"]

    assert user_id == {
        "name": "user_id",
        "in": "path",
        "required": True,
        "schema": {"type": "integer"},
    }


def test_optional_parameters_carry_their_default() -> None:
    by_name = {
        p["name"]: p
        for p in operation_of(document(), "get", "/users/{user_id}")["parameters"]
    }

    assert by_name["expand"]["required"] is False
    assert by_name["expand"]["schema"] == {"type": "boolean", "default": False}
    assert by_name["limit"]["schema"] == {"type": "integer", "default": 10}


def test_list_parameters_are_arrays() -> None:
    by_name = {
        p["name"]: p
        for p in operation_of(document(), "get", "/users/{user_id}")["parameters"]
    }

    assert by_name["tags"]["schema"] == {"type": "array", "items": {"type": "integer"}}
    assert by_name["tags"]["required"] is False


def test_header_parameters_use_their_wire_name() -> None:
    names = [
        p["name"]
        for p in operation_of(document(), "get", "/users/{user_id}")["parameters"]
        if p["in"] == "header"
    ]

    assert names == ["X-Token", "agent"]


def test_an_operation_without_parameters_has_no_parameters_key() -> None:
    assert "parameters" not in operation_of(document(), "get", "/healthz")


def test_request_body_is_required_and_references_its_schema() -> None:
    body = operation_of(document(), "post", "/users")["requestBody"]

    assert body == {
        "required": True,
        "content": {"application/json": {"schema": ref("NewUser")}},
    }


def test_a_body_that_may_be_none_is_not_required() -> None:
    body = operation_of(document(), "put", "/users")["requestBody"]

    assert body["required"] is False
    assert body["content"]["application/json"]["schema"] == {
        "anyOf": [ref("NewUser"), {"type": "null"}]
    }


def test_get_operations_have_no_request_body() -> None:
    assert "requestBody" not in operation_of(document(), "get", "/users/{user_id}")


# --- responses ----------------------------------------------------------


def test_declared_responses_are_keyed_by_status_in_numeric_order() -> None:
    responses = operation_of(document(), "get", "/users/{user_id}")["responses"]

    assert list(responses) == ["200", "401", "403", "404", "422"]


def test_a_json_response_references_its_body_schema() -> None:
    ok = operation_of(document(), "get", "/users/{user_id}")["responses"]["200"]

    assert ok == {
        "description": "OK",
        "content": {"application/json": {"schema": ref("UserOut")}},
    }


def test_a_response_without_a_body_has_no_content() -> None:
    assert operation_of(document(), "get", "/healthz")["responses"]["204"] == {
        "description": "No Content"
    }


def test_response_headers_are_described_with_their_requiredness() -> None:
    created = operation_of(document(), "post", "/users")["responses"]["201"]

    assert created["headers"] == {
        "ETag": {"required": False, "schema": {"type": "string"}},
        "Location": {"required": True, "schema": {"type": "string"}},
    }


def test_a_text_response_uses_its_media_type_without_parameters() -> None:
    motd = operation_of(document(), "get", "/motd")["responses"]["200"]

    assert motd["content"] == {"text/plain": {"schema": {"type": "string"}}}


def test_a_binary_response_is_a_binary_string() -> None:
    blob = operation_of(document(), "get", "/blob")["responses"]["200"]

    assert blob["content"] == {
        "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
    }


def test_status_descriptions_do_not_depend_on_the_python_version() -> None:
    doc = document()

    assert operation_of(doc, "post", "/users")["responses"]["422"]["description"] == (
        "Unprocessable Entity"
    )
    assert (
        operation_of(doc, "get", "/users/{user_id}")["responses"]["404"]["description"]
        == "Not Found"
    )


# --- automatic responses ------------------------------------------------


def test_a_guarded_operation_gets_401_and_403_problem_responses() -> None:
    responses = operation_of(document(), "get", "/either")["responses"]

    for status, title in (("401", "Unauthorized"), ("403", "Forbidden")):
        assert responses[status]["description"] == title
        assert responses[status]["content"] == {
            "application/problem+json": {"schema": ref("ProblemDetails")}
        }


def test_a_public_operation_gets_no_auth_responses() -> None:
    responses = operation_of(document(), "get", "/healthz")["responses"]

    assert list(responses) == ["204"]


def test_an_optional_guard_cannot_refuse_so_it_gets_no_auth_responses() -> None:
    responses = operation_of(document(), "get", "/maybe")["responses"]

    assert list(responses) == ["200"]


def test_a_decoded_request_gets_a_422_with_error_locations() -> None:
    unprocessable = operation_of(document(), "post", "/users")["responses"]["422"]

    assert unprocessable["content"] == {
        "application/problem+json": {"schema": ref("ValidationProblem")}
    }


def test_an_operation_with_no_request_fields_gets_no_422() -> None:
    assert "422" not in operation_of(document(), "get", "/either")["responses"]


class Unauthorized401(NotFound[Problem]):
    status = 401


def test_a_declared_status_is_never_replaced_by_an_automatic_one() -> None:
    class Api(Protocol):
        @op.get("/d", guard=bearer)
        async def declared(
            self, c: Context[User], req: None
        ) -> Ok[int] | Unauthorized401: ...

    responses = operation_of(document(Api), "get", "/d")["responses"]

    assert responses["401"]["content"] == {
        "application/json": {"schema": ref("Problem")}
    }
    assert responses["403"]["content"] == {
        "application/problem+json": {"schema": ref("ProblemDetails")}
    }


def test_problem_schemas_are_only_in_components_when_referenced() -> None:
    public_only = document(_public_contract())

    assert set(public_only.get("components", {}).get("schemas", {})) == set()
    assert {"ProblemDetails", "ValidationProblem", "ValidationIssue"} <= set(
        document()["components"]["schemas"]
    )


def _public_contract() -> type:
    class Api(Protocol):
        @op.get("/ping", guard=public)
        async def ping(self, c: Context[None], req: None) -> NoContent: ...

    return Api


# --- security -----------------------------------------------------------


def test_security_of_a_single_guard() -> None:
    assert operation_of(document(), "get", "/users/{user_id}")["security"] == [
        {"bearer": []}
    ]


def test_security_of_and_leaves_out_guards_without_a_scheme() -> None:
    assert operation_of(document(), "post", "/users")["security"] == [{"bearer": []}]


def test_security_of_or_lists_alternatives() -> None:
    assert operation_of(document(), "get", "/either")["security"] == [
        {"bearer": []},
        {"apikey": []},
    ]


def test_security_of_and_with_two_schemes_requires_both() -> None:
    class Api(Protocol):
        @op.get("/both", guard=bearer & apikey)
        async def both(
            self, c: Context[tuple[User, ApiKey]], req: None
        ) -> NoContent: ...

    assert operation_of(document(Api), "get", "/both")["security"] == [
        {"bearer": [], "apikey": []}
    ]


def test_security_of_or_with_public_allows_anonymous_callers() -> None:
    assert operation_of(document(), "get", "/maybe")["security"] == [
        {"bearer": []},
        {},
    ]


def test_security_of_public_is_explicitly_empty() -> None:
    assert operation_of(document(), "get", "/healthz")["security"] == []


def test_a_guard_without_any_scheme_declares_no_security() -> None:
    assert "security" not in operation_of(document(), "get", "/only-admin")


def test_security_schemes_describe_each_guard_that_has_one() -> None:
    assert document()["components"]["securitySchemes"] == {
        "apikey": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
        "bearer": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"},
    }


def test_a_bearer_scheme_without_a_format_omits_bearer_format() -> None:
    plain = GuardSpec[User]("plain", scheme=HttpBearer())

    class Api(Protocol):
        @op.get("/p", guard=plain)
        async def p(self, c: Context[User], req: None) -> NoContent: ...

    schemes = document(Api)["components"]["securitySchemes"]

    assert schemes == {"plain": {"type": "http", "scheme": "bearer"}}


def test_two_different_schemes_under_one_name_are_a_problem() -> None:
    first = GuardSpec[User]("token", scheme=HttpBearer())
    second = GuardSpec[ApiKey]("token", scheme=ApiKeyHeader("X-Key"))

    class Api(Protocol):
        @op.get("/a", guard=first)
        async def a(self, c: Context[User], req: None) -> NoContent: ...

        @op.get("/b", guard=second)
        async def b(self, c: Context[ApiKey], req: None) -> NoContent: ...

    result, problems = build_openapi(mounted(Api), info=INFO, codec=codec)

    assert result is None
    assert len(problems) == 1
    assert "'token'" in problems[0]


def test_one_guard_used_by_many_operations_is_not_a_clash() -> None:
    doc = document()

    assert list(doc["components"]["securitySchemes"]) == ["apikey", "bearer"]


# --- escape hatch, clashes and determinism ------------------------------


def test_openapi_extra_is_merged_into_the_operation() -> None:
    extra = operation_of(document(), "get", "/extra")

    assert extra["x-internal"] is True
    assert extra["deprecated"] is True


def test_openapi_extra_may_not_override_the_derived_parts() -> None:
    for key in ("parameters", "requestBody", "responses"):

        class Api(Protocol):
            @op.get("/x", guard=public, openapi_extra={key: []})
            async def x(self, c: Context[None], req: None) -> NoContent: ...

        result, problems = build_openapi(mounted(Api), info=INFO, codec=codec)

        assert result is None
        assert len(problems) == 1
        assert key in problems[0]


def test_two_different_types_with_one_name_are_a_problem() -> None:
    other = msgspec.defstruct("UserOut", [("id", str)])

    class Api(Protocol):
        @op.get("/a", guard=public)
        async def a(self, c: Context[None], req: None) -> Ok[UserOut]: ...

        @op.get("/b", guard=public)
        async def b(self, c: Context[None], req: None) -> Ok[other]: ...  # type: ignore[valid-type]

    result, problems = build_openapi(mounted(Api), info=INFO, codec=codec)

    assert result is None
    assert any("UserOut" in p for p in problems)


def test_building_twice_gives_identical_bytes() -> None:
    first = json.dumps(document())
    second = json.dumps(document())

    assert first == second


def test_the_order_operations_were_mounted_in_does_not_matter() -> None:
    forward, _ = build_openapi(mounted(), info=INFO, codec=codec)
    backward, _ = build_openapi(list(reversed(mounted())), info=INFO, codec=codec)

    assert json.dumps(forward) == json.dumps(backward)


def test_component_schemas_are_sorted_by_name() -> None:
    schemas = document()["components"]["schemas"]

    assert list(schemas) == sorted(schemas)


def test_the_document_is_plain_json_data() -> None:
    doc = document()

    assert json.loads(json.dumps(doc)) == doc


def test_the_document_is_valid_openapi_3_1() -> None:
    validate(document(prefix="/v1"))


def test_a_document_with_only_public_operations_is_valid_openapi_3_1() -> None:
    validate(document(_public_contract()))


def test_form_urlencoded_request_body_schema() -> None:
    from lettia import Form

    class FormReq(msgspec.Struct, kw_only=True):
        name: Form[str]
        count: Form[int] = 1

    class FormContract(Protocol):
        @op.post("/submit", guard=public)
        async def submit(self, c: Context[None], req: FormReq) -> Ok[int]: ...

    doc, problems = build_openapi(mounted(FormContract), info=INFO, codec=codec)
    assert not problems
    assert doc is not None
    validate(doc)
    op_spec = doc["paths"]["/submit"]["post"]
    req_body = op_spec["requestBody"]
    assert req_body["required"] is True
    content = req_body["content"]["application/x-www-form-urlencoded"]
    schema = content["schema"]
    assert schema["type"] == "object"
    assert schema["properties"]["name"] == {"type": "string"}
    assert schema["properties"]["count"] == {"type": "integer", "default": 1}
    assert schema["required"] == ["name"]


def test_multipart_file_upload_request_body_schema() -> None:
    from lettia import File, Form, UploadFile

    class UploadReq(msgspec.Struct, kw_only=True):
        title: Form[str]
        avatar: File[UploadFile]
        docs: File[list[UploadFile]] = msgspec.field(default_factory=list)

    class UploadContract(Protocol):
        @op.post("/upload", guard=public)
        async def upload(self, c: Context[None], req: UploadReq) -> Ok[int]: ...

    doc, problems = build_openapi(mounted(UploadContract), info=INFO, codec=codec)
    assert not problems
    assert doc is not None
    validate(doc)
    op_spec = doc["paths"]["/upload"]["post"]
    req_body = op_spec["requestBody"]
    assert req_body["required"] is True
    content = req_body["content"]["multipart/form-data"]
    schema = content["schema"]
    assert schema["type"] == "object"
    assert schema["properties"]["title"] == {"type": "string"}
    assert schema["properties"]["avatar"] == {"type": "string", "format": "binary"}
    assert schema["properties"]["docs"] == {
        "type": "array",
        "items": {"type": "string", "format": "binary"},
    }
    assert schema["required"] == ["avatar", "title"]
