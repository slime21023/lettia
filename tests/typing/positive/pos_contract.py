"""The full contract / implementation shape from the design document."""

from dataclasses import dataclass
from typing import Annotated, Protocol, assert_type

import msgspec

from lettia import (
    App,
    Body,
    Context,
    Created,
    File,
    Form,
    GuardSpec,
    HeaderName,
    HttpBearer,
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


class User(msgspec.Struct):
    uid: int


class Admin(msgspec.Struct): ...


class NewUser(msgspec.Struct):
    name: str


class UserOut(msgspec.Struct):
    id: int


class GetUserReq(msgspec.Struct):
    user_id: Path[int]
    expand: Query[bool] = False


class CreateUserReq(msgspec.Struct):
    body: Body[NewUser]


class UserCreated(Created[UserOut], kw_only=True):
    location: Annotated[str, HeaderName("Location")]


bearer = GuardSpec[User]("bearer", scheme=HttpBearer(format="JWT"))
admin = GuardSpec[Admin]("admin")


class UserApi(Protocol):
    @op.get("/users/{user_id}", guard=bearer, tags=["users"])
    async def get_user(
        self, c: Context[User], req: GetUserReq
    ) -> Ok[UserOut] | NotFound[Problem]:
        """Get a single user."""
        ...

    @op.post("/users", guard=bearer & admin)
    async def create_user(
        self, c: Context[tuple[User, Admin]], req: CreateUserReq
    ) -> UserCreated: ...

    @op.get("/healthz", guard=public)
    async def health(self, c: Context[None], req: None) -> NoContent: ...


@dataclass(frozen=True, slots=True)
class UserService(UserApi):
    users: dict[int, UserOut]

    async def get_user(
        self, c: Context[User], req: GetUserReq
    ) -> Ok[UserOut] | NotFound[Problem]:
        user = self.users.get(req.user_id)
        if user is None:
            return NotFound(Problem(title="user not found"))
        return Ok(user)

    async def create_user(
        self, c: Context[tuple[User, Admin]], req: CreateUserReq
    ) -> UserCreated:
        out = UserOut(id=len(self.users) + 1)
        return UserCreated(out, location=f"/users/{out.id}")

    async def health(self, c: Context[None], req: None) -> NoContent:
        return NoContent()


def check_request_fields(req: GetUserReq, c: Context[User]) -> None:
    assert_type(req.user_id, int)
    assert_type(req.expand, bool)
    assert_type(c.principal, User)


class UploadReq(msgspec.Struct, kw_only=True):
    name: Form[str]
    avatar: File[UploadFile]
    docs: File[list[UploadFile]]


def check_form_fields(req: UploadReq) -> None:
    assert_type(req.name, str)
    assert_type(req.avatar, UploadFile)
    assert_type(req.docs, list[UploadFile])


service = UserService({})


app = App()
app.mount(service)
app.group("/v1").mount(service)
app.group("/v1").group("/admin").mount(service)
