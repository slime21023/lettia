"""In-process ASGI micro-benchmark for the S1, S2 and S3 scenarios.

S1 is a static route returning plain text; S2 is a path parameter with a JSON
response; S3 is the contract path (request decoding, handler, response encoding,
no guard yet) compared with an equivalent hand-written Core handler. Each
application is called directly with a prebuilt scope, so the numbers measure
framework overhead per request without sockets, HTTP parsing or a load generator.
It is a regression guard; the design's acceptance numbers are measured through a
single uvicorn worker.

    uv run python benchmarks/run_benchmark.py [--iterations 100000] [--repeat 5]
"""

import argparse
import asyncio
import time
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Annotated, Any, Protocol

import msgspec
from starlette.applications import Starlette
from starlette.requests import Request as StarletteRequest
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

import lettia
from lettia import Body, Created, HeaderName, op, public
from lettia.core import App, Context, Response

Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
ASGI = Callable[[dict[str, Any], Receive, Any], Awaitable[None]]

REQUEST: Message = {"type": "http.request", "body": b"", "more_body": False}


def scope_for(path: str) -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(b"host", b"localhost"), (b"accept", b"*/*")],
        "server": ("127.0.0.1", 8000),
        "client": ("127.0.0.1", 50000),
    }


async def receive() -> Message:
    return REQUEST


S3_BODY: Message = {
    "type": "http.request",
    "body": b'{"name": "Ann"}',
    "more_body": False,
}


async def receive_json() -> Message:
    return S3_BODY


async def send(message: Message) -> None:
    return None


async def bare_asgi(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """Hand-written floor: what any framework must at least do."""
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", b"2"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": b"ok", "more_body": False})


def lettia_app() -> App:
    async def healthz(c: Context) -> Response:
        return c.text(200, "ok")

    async def user(c: Context) -> Response:
        return c.json(200, {"id": c.path_params["id"], "name": "Ann"})

    app = App()
    app.get("/", healthz)
    app.get("/users/{id}", user)
    app.build()
    return app


def starlette_app() -> Starlette:
    async def healthz(request: StarletteRequest) -> PlainTextResponse:
        return PlainTextResponse("ok")

    async def user(request: StarletteRequest) -> JSONResponse:
        return JSONResponse({"id": request.path_params["id"], "name": "Ann"})

    return Starlette(
        routes=[Route("/", healthz), Route("/users/{id}", user)],
    )


class NewUser(msgspec.Struct):
    name: str


class UserOut(msgspec.Struct):
    id: int
    name: str


class CreateUserReq(msgspec.Struct):
    body: Body[NewUser]


class UserCreated(Created[UserOut], kw_only=True):
    location: Annotated[str, HeaderName("Location")]


class UsersApi(Protocol):
    @op.post("/users", guard=public)
    async def create_user(
        self, c: lettia.Context[None], req: CreateUserReq
    ) -> UserCreated: ...


class UsersService(UsersApi):
    async def create_user(
        self, c: lettia.Context[None], req: CreateUserReq
    ) -> UserCreated:
        return UserCreated(UserOut(1, req.body.name), location="/users/1")


def contract_app() -> lettia.App:
    app = lettia.App()
    app.mount(UsersService())
    app.build()
    return app


def handwritten_app() -> App:
    decode = msgspec.json.Decoder(NewUser).decode

    async def create_user(c: Context) -> Response:
        data = decode(await c.request.body())
        response = c.json(201, UserOut(1, data.name))
        response.headers.set("location", "/users/1")
        return response

    app = App()
    app.post("/users", create_user)
    app.build()
    return app


def scenarios() -> dict[str, tuple[dict[str, Any], Receive, dict[str, ASGI]]]:
    lettia, starlette = lettia_app(), starlette_app()
    return {
        "S1 static route, plain text": (
            scope_for("/"),
            receive,
            {
                "bare ASGI (floor)": bare_asgi,
                "lettia": lettia,
                "starlette": starlette,
            },
        ),
        "S2 path parameter, JSON": (
            scope_for("/users/42"),
            receive,
            {"lettia": lettia, "starlette": starlette},
        ),
        "S3 contract: decode body, JSON response (no guard yet)": (
            {
                **scope_for("/users"),
                "method": "POST",
                "headers": [(b"content-type", b"application/json")],
            },
            receive_json,
            {"hand-written Core": handwritten_app(), "contract": contract_app()},
        ),
    }


async def best_rps(
    app: ASGI, scope: dict[str, Any], receive: Receive, iterations: int, repeat: int
) -> float:
    for _ in range(min(1000, iterations)):  # warm up
        await app(scope, receive, send)
    best = float("inf")
    for _ in range(repeat):
        start = time.perf_counter()
        for _ in range(iterations):
            await app(scope, receive, send)
        best = min(best, time.perf_counter() - start)
    return iterations / best


async def main(iterations: int, repeat: int) -> None:
    print(f"{iterations} calls x {repeat} runs (best run), in-process")
    for title, (scope, receive_fn, apps) in scenarios().items():
        results = {
            name: await best_rps(app, scope, receive_fn, iterations, repeat)
            for name, app in apps.items()
        }
        print(f"\n{title}")
        reference = "starlette" if "starlette" in results else "hand-written Core"
        for name, rps in results.items():
            ratio = rps / results[reference]
            print(f"  {name:<20} {rps:>10,.0f} req/s  ({ratio:>5.2f}x {reference})")
        if reference == "hand-written Core":
            overhead = 1 - results["contract"] / results[reference]
            print(f"  contract overhead vs {reference}: {overhead:.0%} (target <= 25%)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=100_000)
    parser.add_argument("--repeat", type=int, default=5)
    args = parser.parse_args()
    asyncio.run(main(args.iterations, args.repeat))
