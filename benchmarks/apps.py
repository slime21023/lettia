"""The applications the benchmarks compare.

* ``lettia_app``: S1 ``GET /`` (static text), S2 ``GET /users/{id}`` (path
  parameter, JSON) and S3 ``POST /users`` (the full contract path: guard,
  request body decoding, handler, response encoding).
* ``starlette_app``: S1 and S2 on Starlette.
* ``handwritten_app``: S3 written directly against the Core API.

Module-level instances are importable by a server, for example
``uvicorn benchmarks.apps:lettia_app``.
"""

from typing import Annotated, Protocol

import msgspec
from starlette.applications import Starlette
from starlette.requests import Request as StarletteRequest
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

import lettia
from lettia import Body, Created, GuardSpec, HeaderName, RequestHead, op
from lettia.core import App, Context, Response, Unauthorized

# --- S3 contract ----------------------------------------------------------


class NewUser(msgspec.Struct):
    name: str


class UserOut(msgspec.Struct):
    id: int
    name: str


class CreateUserReq(msgspec.Struct):
    body: Body[NewUser]


class UserCreated(Created[UserOut], kw_only=True):
    location: Annotated[str, HeaderName("Location")]


class Principal(msgspec.Struct):
    name: str


bearer = GuardSpec[Principal]("bearer")


async def verify(head: RequestHead) -> Principal:
    if head.headers.get("authorization") != "Bearer good":
        raise Unauthorized("bad token")
    return Principal("ann")


class UsersApi(Protocol):
    @op.post("/users", guard=bearer)
    async def create_user(
        self, c: lettia.Context[Principal], req: CreateUserReq
    ) -> UserCreated: ...


class UsersService(UsersApi):
    async def create_user(
        self, c: lettia.Context[Principal], req: CreateUserReq
    ) -> UserCreated:
        return UserCreated(UserOut(1, req.body.name), location="/users/1")


# --- the applications -----------------------------------------------------


def make_lettia_app() -> lettia.App:
    async def healthz(c: Context) -> Response:
        return c.text(200, "ok")

    async def user(c: Context) -> Response:
        return c.json(200, {"id": c.path_params["id"], "name": "Ann"})

    app = lettia.App(guards=[bearer.use(verify)], openapi_url=None)
    app.get("/", healthz)
    app.get("/users/{id}", user)
    app.mount(UsersService())
    app.build()
    return app


def make_starlette_app() -> Starlette:
    async def healthz(request: StarletteRequest) -> PlainTextResponse:
        return PlainTextResponse("ok")

    async def user(request: StarletteRequest) -> JSONResponse:
        return JSONResponse({"id": request.path_params["id"], "name": "Ann"})

    return Starlette(routes=[Route("/", healthz), Route("/users/{id}", user)])


def make_handwritten_app() -> App:
    decode = msgspec.json.Decoder(NewUser).decode

    async def create_user(c: Context) -> Response:
        await verify(c.request)
        data = decode(await c.request.body())
        response = c.json(201, UserOut(1, data.name))
        response.headers.set("location", "/users/1")
        return response

    app = App()
    app.post("/users", create_user)
    app.build()
    return app


lettia_app = make_lettia_app()
starlette_app = make_starlette_app()
handwritten_app = make_handwritten_app()
