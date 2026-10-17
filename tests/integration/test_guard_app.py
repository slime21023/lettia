"""Guards end to end: which principal reaches the handler, and when requests stop."""

from typing import Protocol

import httpx
import msgspec
import pytest

from lettia import (
    App,
    Body,
    Context,
    Created,
    GuardSpec,
    HttpBearer,
    Ok,
    RequestHead,
    op,
    public,
)
from lettia.core import Forbidden, StartupError, Unauthorized


class User(msgspec.Struct):
    name: str


class Admin(msgspec.Struct):
    level: int


class ApiKey(msgspec.Struct):
    owner: str


class NewItem(msgspec.Struct):
    title: str


class Item(msgspec.Struct):
    title: str


class CreateItem(msgspec.Struct):
    body: Body[NewItem]


bearer = GuardSpec[User]("bearer", scheme=HttpBearer(format="JWT"))
admin = GuardSpec[Admin]("admin")
apikey = GuardSpec[ApiKey]("apikey")


class Calls:
    def __init__(self) -> None:
        self.log: list[str] = []


calls = Calls()


async def verify_bearer(head: RequestHead) -> User:
    calls.log.append("bearer")
    header = head.headers.get("authorization")
    if header is None:
        raise Unauthorized(
            "credentials required", headers={"www-authenticate": "Bearer"}
        )
    if header != "Bearer good":
        raise Unauthorized("bad token")
    return User("ann")


async def require_admin(head: RequestHead) -> Admin:
    calls.log.append("admin")
    if head.headers.get("x-role") != "admin":
        raise Forbidden("admin only")
    return Admin(level=9)


async def lookup_key(head: RequestHead) -> ApiKey:
    calls.log.append("apikey")
    if head.headers.get("x-api-key") != "k1":
        raise Unauthorized("bad key")
    return ApiKey("acme")


class SecureApi(Protocol):
    @op.get("/open", guard=public)
    async def open(self, c: Context[None], req: None) -> Ok[str]: ...

    @op.get("/me", guard=bearer)
    async def me(self, c: Context[User], req: None) -> Ok[str]: ...

    @op.post("/items", guard=bearer)
    async def create_item(self, c: Context[User], req: CreateItem) -> Created[Item]: ...

    @op.get("/admin", guard=bearer & admin)
    async def admin_only(
        self, c: Context[tuple[User, Admin]], req: None
    ) -> Ok[str]: ...

    @op.get("/either", guard=bearer | apikey)
    async def either(self, c: Context[User | ApiKey], req: None) -> Ok[str]: ...

    @op.get("/maybe", guard=bearer | public)
    async def maybe(self, c: Context[User | None], req: None) -> Ok[str]: ...


class SecureService(SecureApi):
    async def open(self, c: Context[None], req: None) -> Ok[str]:
        return Ok("open")

    async def me(self, c: Context[User], req: None) -> Ok[str]:
        return Ok(f"hello {c.principal.name}")

    async def create_item(self, c: Context[User], req: CreateItem) -> Created[Item]:
        return Created(Item(req.body.title))

    async def admin_only(self, c: Context[tuple[User, Admin]], req: None) -> Ok[str]:
        user, level = c.principal
        return Ok(f"{user.name}:{level.level}")

    async def either(self, c: Context[User | ApiKey], req: None) -> Ok[str]:
        who = c.principal
        return Ok(who.name if isinstance(who, User) else who.owner)

    async def maybe(self, c: Context[User | None], req: None) -> Ok[str]:
        return Ok("anonymous" if c.principal is None else c.principal.name)


def build_app() -> App:
    app = App(
        guards=[
            bearer.use(verify_bearer),
            admin.use(require_admin),
            apikey.use(lookup_key),
        ]
    )
    app.mount(SecureService())
    return app


@pytest.fixture(autouse=True)
def reset_calls() -> None:
    calls.log.clear()


def client(app: App) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


GOOD = {"authorization": "Bearer good"}


# --- single guards ------------------------------------------------------


async def test_the_handler_receives_the_principal_from_its_guard() -> None:
    async with client(build_app()) as http:
        res = await http.get("/me", headers=GOOD)

    assert (res.status_code, res.json()) == (200, "hello ann")


async def test_a_refused_request_is_a_401_with_the_guards_headers() -> None:
    async with client(build_app()) as http:
        res = await http.get("/me")

    assert res.status_code == 401
    assert res.headers["www-authenticate"] == "Bearer"
    assert res.json()["detail"] == "credentials required"


async def test_a_public_route_never_runs_any_guard() -> None:
    async with client(build_app()) as http:
        res = await http.get("/open")

    assert (res.status_code, calls.log) == (200, [])


async def test_guards_run_before_the_body_is_decoded() -> None:
    async with client(build_app()) as http:
        unauthenticated = await http.post("/items", json={"title": 5})
        authenticated = await http.post("/items", json={"title": 5}, headers=GOOD)

    assert unauthenticated.status_code == 401
    assert "errors" not in unauthenticated.json()
    assert authenticated.status_code == 422


async def test_an_authenticated_request_with_a_valid_body_succeeds() -> None:
    async with client(build_app()) as http:
        res = await http.post("/items", json={"title": "pen"}, headers=GOOD)

    assert (res.status_code, res.json()) == (201, {"title": "pen"})


# --- composition --------------------------------------------------------


async def test_and_gives_the_handler_both_principals_in_order() -> None:
    async with client(build_app()) as http:
        res = await http.get("/admin", headers={**GOOD, "x-role": "admin"})

    assert (res.status_code, res.json()) == (200, "ann:9")
    assert calls.log == ["bearer", "admin"]


async def test_and_refuses_with_the_status_of_the_first_failing_guard() -> None:
    async with client(build_app()) as http:
        forbidden = await http.get("/admin", headers=GOOD)
        calls.log.clear()
        unauthorized = await http.get("/admin", headers={"x-role": "admin"})

    assert forbidden.status_code == 403
    assert unauthorized.status_code == 401
    assert calls.log == ["bearer"]  # the admin guard did not run


async def test_or_accepts_either_credential() -> None:
    async with client(build_app()) as http:
        by_token = await http.get("/either", headers=GOOD)
        by_key = await http.get("/either", headers={"x-api-key": "k1"})

    assert by_token.json() == "ann"
    assert by_key.json() == "acme"


async def test_or_stops_at_the_first_branch_that_succeeds() -> None:
    async with client(build_app()) as http:
        await http.get("/either", headers=GOOD)

    assert calls.log == ["bearer"]


async def test_or_with_every_branch_refused_is_a_401() -> None:
    async with client(build_app()) as http:
        res = await http.get("/either")

    assert res.status_code == 401
    assert calls.log == ["bearer", "apikey"]


async def test_or_with_public_allows_anonymous_callers() -> None:
    async with client(build_app()) as http:
        anonymous = await http.get("/maybe")
        known = await http.get("/maybe", headers=GOOD)

    assert (anonymous.json(), known.json()) == ("anonymous", "ann")


async def test_a_crashing_guard_is_a_500_not_a_refusal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def broken(head: RequestHead) -> User:
        raise RuntimeError("key service unreachable")

    app = App(
        guards=[bearer.use(broken), apikey.use(lookup_key), admin.use(require_admin)]
    )
    app.mount(SecureService())

    async with client(app) as http:
        res = await http.get("/either", headers={"x-api-key": "k1"})

    assert res.status_code == 500
    assert "apikey" not in calls.log


# --- startup ------------------------------------------------------------


def test_a_guard_without_an_implementation_stops_startup() -> None:
    app = App(guards=[bearer.use(verify_bearer)])
    app.mount(SecureService())

    with pytest.raises(StartupError) as caught:
        app.build()

    problems = caught.value.problems
    assert any("'admin'" in p and "SecureApi.admin_only" in p for p in problems)
    assert any("'apikey'" in p and "SecureApi.either" in p for p in problems)
    assert not any("'bearer'" in p for p in problems)


def test_a_guard_implemented_twice_stops_startup() -> None:
    app = App(guards=[bearer.use(verify_bearer), bearer.use(verify_bearer)])

    with pytest.raises(StartupError) as caught:
        app.build()

    assert any("'bearer'" in p and "more than once" in p for p in caught.value.problems)


def test_guards_nobody_uses_are_not_an_error() -> None:
    app = App(guards=[bearer.use(verify_bearer)])

    app.build()


def test_app_rejects_guards_that_are_not_implementations() -> None:
    with pytest.raises(TypeError, match="use"):
        App(guards=[bearer])  # type: ignore[list-item]
