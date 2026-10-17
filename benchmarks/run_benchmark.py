"""In-process ASGI micro-benchmark for the S1 and S2 scenarios.

S1 is a static route returning plain text; S2 is a path parameter with a JSON
response. Each application is called directly with a prebuilt scope, so the
numbers measure framework overhead per request without sockets, HTTP parsing or
a load generator. It is a regression guard; the design's acceptance numbers are
measured through a single uvicorn worker.

    uv run python benchmarks/run_benchmark.py [--iterations 100000] [--repeat 5]
"""

import argparse
import asyncio
import time
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request as StarletteRequest
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

from lettia.core import App, Context, Response

Message = MutableMapping[str, Any]
ASGI = Callable[
    [dict[str, Any], Callable[[], Awaitable[Message]], Any], Awaitable[None]
]

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


def scenarios() -> dict[str, tuple[dict[str, Any], dict[str, ASGI]]]:
    lettia, starlette = lettia_app(), starlette_app()
    return {
        "S1 static route, plain text": (
            scope_for("/"),
            {
                "bare ASGI (floor)": bare_asgi,
                "lettia": lettia,
                "starlette": starlette,
            },
        ),
        "S2 path parameter, JSON": (
            scope_for("/users/42"),
            {"lettia": lettia, "starlette": starlette},
        ),
    }


async def best_rps(
    app: ASGI, scope: dict[str, Any], iterations: int, repeat: int
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
    for title, (scope, apps) in scenarios().items():
        results = {
            name: await best_rps(app, scope, iterations, repeat)
            for name, app in apps.items()
        }
        print(f"\n{title}")
        for name, rps in results.items():
            ratio = rps / results["starlette"]
            print(f"  {name:<20} {rps:>10,.0f} req/s  ({ratio:>5.2f}x starlette)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=100_000)
    parser.add_argument("--repeat", type=int, default=5)
    args = parser.parse_args()
    asyncio.run(main(args.iterations, args.repeat))
