"""A session read by a contract guard: login, protected operation, OpenAPI."""

from typing import Protocol, override

import msgspec

from lettia import ApiKeyCookie, App, Context, GuardSpec, NoContent, Ok, op, public
from lettia.core import RequestHead, Unauthorized
from lettia.extensions.sessions import Session, SessionMiddleware
from lettia.testing import TestClient

KEY = b"k" * 32


class User(msgspec.Struct):
    name: str


sessions = SessionMiddleware([KEY])
logged_in = GuardSpec[User]("session", scheme=ApiKeyCookie("lettia_session"))


async def verify_session(head: RequestHead) -> User:
    name = sessions.read(head).get("user")
    if not isinstance(name, str):
        raise Unauthorized("sign in")
    return User(name)


class Api(Protocol):
    @op.post("/login", guard=public)
    async def login(self, c: Context[None], req: None) -> NoContent: ...

    @op.get("/me", guard=logged_in)
    async def me(self, c: Context[User], req: None) -> Ok[str]: ...


class Service(Api):
    @override
    async def login(self, c: Context[None], req: None) -> NoContent:
        Session.of(c)["user"] = "ann"
        return NoContent()

    @override
    async def me(self, c: Context[User], req: None) -> Ok[str]:
        return Ok(c.principal.name)


def make_app() -> App:
    app = App(guards=[logged_in.use(verify_session)])
    app.use(sessions)
    app.mount(Service())
    return app


def client(app: App) -> TestClient:
    return TestClient(app, base_url="https://testserver")


async def test_a_session_set_at_login_authenticates_a_guarded_operation() -> None:
    async with client(make_app()) as http:
        before = await http.get("/me")
        await http.post("/login")
        after = await http.get("/me")

    assert before.status_code == 401
    assert after.status_code == 200
    assert after.json() == "ann"


async def test_a_forged_session_cookie_does_not_authenticate() -> None:
    app = make_app()
    async with client(app) as http:
        await http.post("/login")
        token = http.cookies["lettia_session"]
    forged = token[:-2] + ("AA" if not token.endswith("AA") else "BB")

    async with client(app) as stranger:
        res = await stranger.get("/me", headers={"cookie": f"lettia_session={forged}"})

    assert res.status_code == 401


async def test_the_guard_does_not_write_or_refresh_the_session_cookie() -> None:
    async with client(make_app()) as http:
        await http.post("/login")
        res = await http.get("/me")

    assert res.headers.get_list("set-cookie") == []


def test_openapi_describes_the_cookie_scheme_and_the_security_requirement() -> None:
    document = make_app().openapi()

    assert document["components"]["securitySchemes"]["session"] == {
        "type": "apiKey",
        "in": "cookie",
        "name": "lettia_session",
    }
    assert document["paths"]["/me"]["get"]["security"] == [{"session": []}]
    assert document["paths"]["/login"]["post"]["security"] == []
