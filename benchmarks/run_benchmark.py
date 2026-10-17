"""In-process ASGI micro-benchmark for the S1 scenario (static route, plain text).

Calls each application directly with a prebuilt scope, so it measures framework
overhead per request without sockets, HTTP parsing or a load generator. It is a
regression guard and a first baseline; the design's acceptance numbers are
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
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from lettia.core import Context, Response
from lettia.core.asgi import asgi_app

Message = MutableMapping[str, Any]
ASGI = Callable[
    [dict[str, Any], Callable[[], Awaitable[Message]], Any], Awaitable[None]
]

SCOPE: dict[str, Any] = {
    "type": "http",
    "asgi": {"version": "3.0"},
    "http_version": "1.1",
    "method": "GET",
    "scheme": "http",
    "path": "/",
    "raw_path": b"/",
    "root_path": "",
    "query_string": b"",
    "headers": [(b"host", b"localhost"), (b"accept", b"*/*")],
    "server": ("127.0.0.1", 8000),
    "client": ("127.0.0.1", 50000),
}
REQUEST: Message = {"type": "http.request", "body": b"", "more_body": False}


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


async def lettia_handler(c: Context) -> Response:
    return c.text(200, "ok")


async def starlette_endpoint(request: StarletteRequest) -> PlainTextResponse:
    return PlainTextResponse("ok")


def build_apps() -> dict[str, ASGI]:
    return {
        "bare ASGI (floor)": bare_asgi,
        "lettia (adapter only)": asgi_app(lettia_handler),
        "starlette (full app)": Starlette(routes=[Route("/", starlette_endpoint)]),
    }


async def best_rps(app: ASGI, iterations: int, repeat: int) -> float:
    for _ in range(min(1000, iterations)):  # warm up
        await app(SCOPE, receive, send)
    best = float("inf")
    for _ in range(repeat):
        start = time.perf_counter()
        for _ in range(iterations):
            await app(SCOPE, receive, send)
        best = min(best, time.perf_counter() - start)
    return iterations / best


async def main(iterations: int, repeat: int) -> None:
    results = {
        name: await best_rps(app, iterations, repeat)
        for name, app in build_apps().items()
    }
    floor = results["bare ASGI (floor)"]
    print(f"S1 static text, {iterations} calls x {repeat} runs (best run), in-process")
    for name, rps in results.items():
        print(f"  {name:<24} {rps:>10,.0f} req/s  ({rps / floor:>5.0%} of floor)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=100_000)
    parser.add_argument("--repeat", type=int, default=5)
    args = parser.parse_args()
    asyncio.run(main(args.iterations, args.repeat))
