"""In-process ASGI micro-benchmark for the S1, S2 and S3 scenarios.

S1 is a static route returning plain text; S2 is a path parameter with a JSON
response; S3 is the full contract path (guard, request decoding, handler,
response encoding) compared with an equivalent hand-written Core handler. Each
application is called directly with a prebuilt scope, so the numbers measure
framework overhead per request without sockets, HTTP parsing or a load
generator. It is a regression guard; the design's acceptance numbers come from
``run_server_benchmark.py``, which goes through a real server.

    uv run python benchmarks/run_benchmark.py [--iterations 100000] [--repeat 5]
"""

import argparse
import asyncio
import time
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from benchmarks.apps import handwritten_app, lettia_app, starlette_app

Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
ASGI = Callable[[dict[str, Any], Receive, Any], Awaitable[None]]

REQUEST: Message = {"type": "http.request", "body": b"", "more_body": False}
S3_BODY: Message = {
    "type": "http.request",
    "body": b'{"name": "Ann"}',
    "more_body": False,
}


def scope_for(path: str, method: str = "GET", **extra: Any) -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(b"host", b"localhost"), (b"accept", b"*/*")],
        "server": ("127.0.0.1", 8000),
        "client": ("127.0.0.1", 50000),
        **extra,
    }


async def receive() -> Message:
    return REQUEST


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


def scenarios() -> dict[str, tuple[dict[str, Any], Receive, dict[str, ASGI]]]:
    s3_headers = [
        (b"content-type", b"application/json"),
        (b"authorization", b"Bearer good"),
    ]
    return {
        "S1 static route, plain text": (
            scope_for("/"),
            receive,
            {
                "bare ASGI (floor)": bare_asgi,
                "lettia": lettia_app,
                "starlette": starlette_app,
            },
        ),
        "S2 path parameter, JSON": (
            scope_for("/users/42"),
            receive,
            {"lettia": lettia_app, "starlette": starlette_app},
        ),
        "S3 contract: guard, decode body, JSON response": (
            scope_for("/users", "POST", headers=s3_headers),
            receive_json,
            {"hand-written Core": handwritten_app, "contract": lettia_app},
        ),
    }


async def best_rates(
    apps: dict[str, ASGI],
    scope: dict[str, Any],
    receive: Receive,
    iterations: int,
    repeat: int,
) -> dict[str, float]:
    """Requests per second of each app, best of ``repeat`` interleaved rounds.

    Alternating between the apps inside every round keeps slow drift of the
    machine (frequency scaling, other processes) from favouring whichever app
    happens to be measured first.
    """
    for app in apps.values():  # warm up
        for _ in range(min(1000, iterations)):
            await app(scope, receive, send)
    best = dict.fromkeys(apps, float("inf"))
    for _ in range(repeat):
        for name, app in apps.items():
            start = time.perf_counter()
            for _ in range(iterations):
                await app(scope, receive, send)
            best[name] = min(best[name], time.perf_counter() - start)
    return {name: iterations / seconds for name, seconds in best.items()}


async def main(iterations: int, repeat: int) -> None:
    print(f"{iterations} calls x {repeat} interleaved rounds (best), in-process")
    for title, (scope, receive_fn, apps) in scenarios().items():
        results = await best_rates(apps, scope, receive_fn, iterations, repeat)
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
