"""A small users API: the contract, its implementation and the application.

Run it:

    uv run uvicorn examples.users_api:app --reload

Try it (the demo tokens are `user-token` and `admin-token`):

    curl -H "Authorization: Bearer user-token" localhost:8000/v1/users/1
    curl localhost:8000/openapi.json

The OpenAPI document is derived from the contract below and committed as
`examples/openapi.json`; regenerate it with

    uv run lettia openapi export examples.users_api:app --output examples/openapi.json
"""

from dataclasses import dataclass
from typing import Annotated, Protocol, override

import msgspec

from lettia import (
    App,
    Body,
    Context,
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
    RequestHead,
    op,
    public,
)
from lettia.core import Forbidden, Unauthorized

# --- models -------------------------------------------------------------


class Caller(msgspec.Struct):
    """Who is calling, as established by the bearer guard."""

    name: str


class Admin(msgspec.Struct):
    """Proof that the caller may change data."""

    level: int


class NewUser(msgspec.Struct):
    name: str
    email: str


class UserOut(msgspec.Struct):
    id: int
    name: str
    email: str


# --- requests and responses ---------------------------------------------


class ListUsersReq(msgspec.Struct):
    limit: Query[int] = 20
    after: Query[int | None] = None
    trace: Header[Annotated[str, HeaderName("X-Trace-Id")]] = ""


class GetUserReq(msgspec.Struct):
    user_id: Path[int]


class CreateUserReq(msgspec.Struct):
    body: Body[NewUser]


class UserCreated(Created[UserOut], kw_only=True):
    location: Annotated[str, HeaderName("Location")]


# --- guards: declared with the contract ---------------------------------

bearer = GuardSpec[Caller]("bearer", scheme=HttpBearer(format="JWT"))
admin = GuardSpec[Admin]("admin")


# --- the contract -------------------------------------------------------


class UsersApi(Protocol):
    @op.get("/users", guard=bearer, tags=["users"])
    async def list_users(
        self, c: Context[Caller], req: ListUsersReq
    ) -> Ok[list[UserOut]]:
        """List users, oldest first."""
        ...

    @op.get("/users/{user_id}", guard=bearer, tags=["users"])
    async def get_user(
        self, c: Context[Caller], req: GetUserReq
    ) -> Ok[UserOut] | NotFound[Problem]:
        """Get a single user."""
        ...

    @op.post("/users", guard=bearer & admin, tags=["users"])
    async def create_user(
        self, c: Context[tuple[Caller, Admin]], req: CreateUserReq
    ) -> UserCreated:
        """Create a user."""
        ...

    @op.delete("/users/{user_id}", guard=bearer & admin, tags=["users"])
    async def delete_user(
        self, c: Context[tuple[Caller, Admin]], req: GetUserReq
    ) -> NoContent | NotFound[Problem]:
        """Delete a user."""
        ...


class SystemApi(Protocol):
    @op.get("/healthz", guard=public, tags=["system"])
    async def health(self, c: Context[None], req: None) -> Ok[str]:
        """Liveness probe."""
        ...


# --- the implementation: dependencies are plain fields ------------------


@dataclass(frozen=True, slots=True)
class UserService(UsersApi):
    users: dict[int, UserOut]

    @override
    async def list_users(
        self, c: Context[Caller], req: ListUsersReq
    ) -> Ok[list[UserOut]]:
        ordered = sorted(self.users.values(), key=lambda user: user.id)
        newer = [u for u in ordered if req.after is None or u.id > req.after]
        return Ok(newer[: req.limit])

    @override
    async def get_user(
        self, c: Context[Caller], req: GetUserReq
    ) -> Ok[UserOut] | NotFound[Problem]:
        user = self.users.get(req.user_id)
        if user is None:
            return NotFound(Problem(title="user not found"))
        return Ok(user)

    @override
    async def create_user(
        self, c: Context[tuple[Caller, Admin]], req: CreateUserReq
    ) -> UserCreated:
        new_id = max(self.users, default=0) + 1
        user = UserOut(new_id, req.body.name, req.body.email)
        self.users[new_id] = user
        return UserCreated(user, location=f"/v1/users/{new_id}")

    @override
    async def delete_user(
        self, c: Context[tuple[Caller, Admin]], req: GetUserReq
    ) -> NoContent | NotFound[Problem]:
        if self.users.pop(req.user_id, None) is None:
            return NotFound(Problem(title="user not found"))
        return NoContent()


class SystemService(SystemApi):
    @override
    async def health(self, c: Context[None], req: None) -> Ok[str]:
        return Ok("ok")


# --- guards: implemented where the secrets are --------------------------

TOKENS = {"user-token": "ann", "admin-token": "root"}


async def verify_bearer(head: RequestHead) -> Caller:
    header = head.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or token not in TOKENS:
        raise Unauthorized("a valid bearer token is required")
    return Caller(TOKENS[token])


async def require_admin(head: RequestHead) -> Admin:
    header = head.headers.get("authorization", "")
    if header.partition(" ")[2] != "admin-token":
        raise Forbidden("administrators only")
    return Admin(level=1)


# --- assembly -----------------------------------------------------------

app = App(
    title="Users API",
    version="0.1.0",
    description="Contract-first example for lettia.",
    guards=[bearer.use(verify_bearer), admin.use(require_admin)],
)
app.group("/v1").mount(UserService({1: UserOut(1, "Ann", "ann@example.com")}))
app.mount(SystemService())
