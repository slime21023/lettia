"""The design document's UserApi example, end to end through ASGI."""

import logging
from dataclasses import dataclass
from typing import Annotated, Protocol, override

import httpx
import msgspec
import pytest

from lettia import (
    App,
    Body,
    Context,
    Created,
    File,
    Form,
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
from lettia.core import Context as CoreContext
from lettia.core import Handler, HTTPError, Middleware, Response, StartupError


class NewUser(msgspec.Struct):
    name: str


class UserOut(msgspec.Struct):
    id: int
    name: str


class GetUserReq(msgspec.Struct):
    user_id: Path[int]
    expand: Query[bool] = False


class CreateUserReq(msgspec.Struct):
    body: Body[NewUser]


class UserCreated(Created[UserOut], kw_only=True):
    location: Annotated[str, HeaderName("Location")]


class UserApi(Protocol):
    @op.get("/users/{user_id}", guard=public, tags=["users"])
    async def get_user(
        self, c: Context[None], req: GetUserReq
    ) -> Ok[UserOut] | NotFound[Problem]:
        """Get a single user."""
        ...

    @op.post("/users", guard=public)
    async def create_user(
        self, c: Context[None], req: CreateUserReq
    ) -> UserCreated: ...

    @op.get("/healthz", guard=public)
    async def health(self, c: Context[None], req: None) -> NoContent: ...


class AuditApi(Protocol):
    @op.get("/audit/whoami", guard=public)
    async def whoami(self, c: Context[None], req: None) -> Ok[str]: ...


@dataclass(frozen=True, slots=True)
class UserService(UserApi):
    users: dict[int, UserOut]

    async def get_user(
        self, c: Context[None], req: GetUserReq
    ) -> Ok[UserOut] | NotFound[Problem]:
        user = self.users.get(req.user_id)
        if user is None:
            return NotFound(Problem(title="user not found"))
        return Ok(user)

    async def create_user(self, c: Context[None], req: CreateUserReq) -> UserCreated:
        out = UserOut(id=len(self.users) + 1, name=req.body.name)
        return UserCreated(out, location=f"/v1/users/{out.id}")

    async def health(self, c: Context[None], req: None) -> NoContent:
        return NoContent()


class Everything(UserService, AuditApi):
    """One implementation of two contracts."""

    async def whoami(self, c: Context[None], req: None) -> Ok[str]:
        return Ok(f"principal={c.principal!r}")


def client(app: App) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


def build_app(*middleware: Middleware) -> App:
    app = App()
    group = app.group("/v1", *middleware)
    group.mount(UserService({1: UserOut(1, "Ann")}))
    return app


# --- happy paths --------------------------------------------------------


async def test_get_returns_the_declared_ok_response() -> None:
    async with client(build_app()) as http:
        res = await http.get("/v1/users/1")

    assert res.status_code == 200
    assert res.headers["content-type"] == "application/json"
    assert res.json() == {"id": 1, "name": "Ann"}


async def test_get_returns_the_other_member_of_the_union() -> None:
    async with client(build_app()) as http:
        res = await http.get("/v1/users/99")

    assert res.status_code == 404
    assert res.json()["title"] == "user not found"


async def test_post_decodes_the_body_and_sets_the_declared_header() -> None:
    async with client(build_app()) as http:
        res = await http.post("/v1/users", json={"name": "Bob"})

    assert res.status_code == 201
    assert res.headers["location"] == "/v1/users/2"
    assert res.json() == {"id": 2, "name": "Bob"}


async def test_operation_without_a_request_returns_no_content() -> None:
    async with client(build_app()) as http:
        res = await http.get("/v1/healthz")

    assert res.status_code == 204
    assert res.content == b""


async def test_query_parameters_reach_the_handler_converted() -> None:
    seen: list[bool] = []

    class Spy(UserService):
        @override
        async def get_user(
            self, c: Context[None], req: GetUserReq
        ) -> Ok[UserOut] | NotFound[Problem]:
            seen.append(req.expand)
            return NotFound(Problem(title="x"))

    app = App()
    app.group("/v1").mount(Spy({}))

    async with client(app) as http:
        await http.get("/v1/users/1?expand=true")
        await http.get("/v1/users/1")

    assert seen == [True, False]


async def test_head_is_answered_for_a_contract_get_route() -> None:
    async with client(build_app()) as http:
        res = await http.head("/v1/users/1")

    assert res.status_code == 200
    assert res.content == b""


async def test_handlers_get_a_context_with_an_empty_principal_for_public_routes() -> (
    None
):
    app = App()
    app.mount(Everything({}))

    async with client(app) as http:
        res = await http.get("/audit/whoami")

    assert res.json() == "principal=None"


async def test_one_implementation_can_serve_several_contracts() -> None:
    app = App()
    app.mount(Everything({1: UserOut(1, "Ann")}))

    async with client(app) as http:
        assert (await http.get("/users/1")).status_code == 200
        assert (await http.get("/audit/whoami")).status_code == 200


def test_the_same_contract_cannot_be_mounted_twice_in_one_app() -> None:
    # Both mounts would declare the same operationIds, which OpenAPI forbids.
    app = App()
    app.group("/v1").mount(UserService({}))
    app.group("/v2").mount(UserService({}))

    with pytest.raises(StartupError) as caught:
        app.build()

    assert any("operationId 'get_user'" in p for p in caught.value.problems)


async def test_group_middleware_applies_to_mounted_routes() -> None:
    def tag(next_: Handler) -> Handler:
        async def run(c: CoreContext) -> Response:
            response = await next_(c)
            response.headers.set("x-tag", "yes")
            return response

        return run

    async with client(build_app(tag)) as http:
        res = await http.get("/v1/users/1")

    assert res.headers["x-tag"] == "yes"


# --- invalid requests and server errors ---------------------------------


async def test_invalid_body_is_a_422_problem_located_by_path() -> None:
    async with client(build_app()) as http:
        res = await http.post("/v1/users", json={"name": 5})

    assert res.status_code == 422
    assert res.headers["content-type"] == "application/problem+json"
    assert res.json()["errors"][0]["path"] == "$.body.name"


async def test_invalid_path_parameter_is_a_422_problem() -> None:
    async with client(build_app()) as http:
        res = await http.get("/v1/users/abc")

    assert res.status_code == 422
    assert res.json()["errors"][0]["path"] == "$.path.user_id"


async def test_a_non_json_body_is_a_415() -> None:
    async with client(build_app()) as http:
        res = await http.post(
            "/v1/users", content=b"name=Bob", headers={"content-type": "text/plain"}
        )

    assert res.status_code == 415


async def test_unknown_path_and_wrong_method_use_the_core_answers() -> None:
    async with client(build_app()) as http:
        missing = await http.get("/v1/nothing")
        wrong = await http.delete("/v1/users/1")

    assert missing.status_code == 404
    assert wrong.status_code == 405
    assert wrong.headers["allow"] == "GET, HEAD"


async def test_a_handler_may_raise_an_http_error() -> None:
    class Forbidding(UserService):
        @override
        async def health(self, c: Context[None], req: None) -> NoContent:
            raise HTTPError(409, "Conflict", "busy")

    app = App()
    app.mount(Forbidding({}))

    async with client(app) as http:
        res = await http.get("/healthz")

    assert (res.status_code, res.json()["detail"]) == (409, "busy")


async def test_a_handler_returning_an_undeclared_class_is_a_logged_500(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Wrong(UserService):
        @override
        async def health(self, c: Context[None], req: None) -> NoContent:
            return Ok(1)  # type: ignore[return-value]

    app = App()
    app.mount(Wrong({}))

    with caplog.at_level(logging.ERROR, logger="lettia"):
        async with client(app) as http:
            res = await http.get("/healthz")

    assert res.status_code == 500
    assert any("does not declare" in str(r.exc_info) for r in caplog.records)


class Mismatched(UserService):
    @override
    async def get_user(
        self, c: Context[None], req: GetUserReq
    ) -> Ok[UserOut] | NotFound[Problem]:
        return Ok({"id": "not an int", "name": "x"})  # type: ignore[arg-type]


async def test_debug_mode_turns_a_body_that_breaks_the_contract_into_a_500() -> None:
    app = App(debug=True)
    app.mount(Mismatched({}))

    async with client(app) as http:
        res = await http.get("/users/1")

    assert res.status_code == 500
    assert "does not match" in res.json()["detail"]


async def test_production_mode_does_not_pay_for_the_body_check() -> None:
    app = App()
    app.mount(Mismatched({}))

    async with client(app) as http:
        res = await http.get("/users/1")

    assert res.status_code == 200


class Unlocated(UserService):
    @override
    async def create_user(self, c: Context[None], req: CreateUserReq) -> UserCreated:
        return UserCreated(UserOut(1, "x"), location=None)  # type: ignore[arg-type]


async def test_a_missing_required_response_header_is_a_logged_500(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = App()
    app.mount(Unlocated({}))

    with caplog.at_level(logging.ERROR, logger="lettia"):
        async with client(app) as http:
            res = await http.post("/users", json={"name": "Bo"})

    assert res.status_code == 500
    assert any("required header" in str(r.exc_info) for r in caplog.records)


class FormPostReq(msgspec.Struct, kw_only=True):
    name: Form[str]
    count: Form[int] = 1


class FormPostResult(msgspec.Struct):
    greeting: str
    total: int


class MultipartUploadReq(msgspec.Struct, kw_only=True):
    category: Form[str]
    avatar: File[UploadFile]
    docs: File[list[UploadFile]] = msgspec.field(default_factory=list)


class MultipartUploadResult(msgspec.Struct):
    category: str
    avatar_name: str
    avatar_bytes: int
    doc_count: int


class FormAndFileApi(Protocol):
    @op.post("/submit-form", guard=public)
    async def submit_form(
        self, c: Context[None], req: FormPostReq
    ) -> Ok[FormPostResult]: ...

    @op.post("/upload-files", guard=public)
    async def upload_files(
        self, c: Context[None], req: MultipartUploadReq
    ) -> Ok[MultipartUploadResult]: ...


class FormAndFileService(FormAndFileApi):
    async def submit_form(
        self, c: Context[None], req: FormPostReq
    ) -> Ok[FormPostResult]:
        return Ok(FormPostResult(f"Hello, {req.name}", req.count * 2))

    async def upload_files(
        self, c: Context[None], req: MultipartUploadReq
    ) -> Ok[MultipartUploadResult]:
        content = await req.avatar.read()
        return Ok(
            MultipartUploadResult(
                category=req.category,
                avatar_name=req.avatar.filename or "",
                avatar_bytes=len(content),
                doc_count=len(req.docs),
            )
        )


async def test_form_urlencoded_end_to_end() -> None:
    app = App()
    app.mount(FormAndFileService())

    async with client(app) as http:
        res = await http.post("/submit-form", data={"name": "Bob", "count": "5"})

    assert res.status_code == 200
    assert res.json() == {"greeting": "Hello, Bob", "total": 10}


async def test_multipart_file_upload_end_to_end() -> None:
    app = App()
    app.mount(FormAndFileService())

    files = [
        ("avatar", ("me.png", b"fake-png-content", "image/png")),
        ("docs", ("doc1.txt", b"doc 1 content", "text/plain")),
        ("docs", ("doc2.txt", b"doc 2 content", "text/plain")),
    ]
    data = {"category": "profile"}

    async with client(app) as http:
        res = await http.post("/upload-files", data=data, files=files)

    assert res.status_code == 200
    assert res.json() == {
        "category": "profile",
        "avatar_name": "me.png",
        "avatar_bytes": 16,
        "doc_count": 2,
    }


async def test_multipart_file_upload_missing_required_file_422() -> None:
    app = App()
    app.mount(FormAndFileService())

    data = {"category": "profile"}

    async with client(app) as http:
        res = await http.post("/upload-files", data=data)

    assert res.status_code == 422
    assert "avatar" in res.json()["detail"]
