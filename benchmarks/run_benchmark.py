import asyncio
import dataclasses
import time
from typing import Any

from attrs import define

from lettia import App, Context, Response, Router
from lettia.middleware import (
    MemoryRateLimiter,
    build_chain,
)
from lettia.middleware.session import _sign, _unsign
from lettia.response import normalize_response


@define(slots=True)
class UserAttrs:
    name: str
    age: int = 18


@dataclasses.dataclass
class UserDC:
    name: str
    age: int = 18


def benchmark_routing_performance(iterations: int = 100000) -> None:
    router = Router()
    router.add_route("GET", "/users", "static_users")
    router.add_route("GET", "/users/:id", "get_user_id")
    router.add_route("GET", "/files/*filepath", "static_files")

    # 1. Static Route Benchmark
    start = time.perf_counter()
    for _ in range(iterations):
        _ = router.match("GET", "/users")
    duration_static = time.perf_counter() - start
    rps_static = iterations / duration_static

    # 2. Parameterized Route Benchmark
    start = time.perf_counter()
    for _ in range(iterations):
        _ = router.match("GET", "/users/12345")
    duration_param = time.perf_counter() - start
    rps_param = iterations / duration_param

    print("--- 1. Router Matching Benchmark ---")
    print(
        f"Static Route Match:      {rps_static:,.0f} ops/sec "
        f"({duration_static * 1000:.2f}ms for {iterations:,} ops)"
    )
    print(
        f"Param Route Match:       {rps_param:,.0f} ops/sec "
        f"({duration_param * 1000:.2f}ms for {iterations:,} ops)"
    )


def benchmark_context_allocation(iterations: int = 100000) -> None:
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/resource",
        "query_string": b"foo=bar&baz=qux",
        "headers": [(b"user-agent", b"benchmark")],
    }

    start = time.perf_counter()
    for _ in range(iterations):
        _ = Context(scope=scope, receive=None, send=None)
    duration = time.perf_counter() - start
    ops_sec = iterations / duration

    print("\n--- 2. Context Allocation Benchmark ---")
    print(
        f"Context Instance Creation: {ops_sec:,.0f} ops/sec "
        f"({duration * 1000:.2f}ms for {iterations:,} ops)"
    )


def benchmark_middleware_chain(iterations: int = 50000) -> None:
    def mw1(next_h: Any) -> Any:
        async def handler(ctx: Any) -> Any:
            return await next_h(ctx)

        return handler

    def mw2(next_h: Any) -> Any:
        async def handler(ctx: Any) -> Any:
            return await next_h(ctx)

        return handler

    async def base_handler(ctx: Any) -> Any:
        return Response(body=b"OK")

    chain = build_chain(base_handler, [mw1, mw2])
    ctx = Context(scope={"type": "http"}, receive=None, send=None)

    async def run_loop() -> None:
        start = time.perf_counter()
        for _ in range(iterations):
            await chain(ctx)
        duration = time.perf_counter() - start
        ops_sec = iterations / duration
        print("\n--- 3. Middleware Chain Benchmark ---")
        print(
            f"Compiled Chain Execution:  {ops_sec:,.0f} ops/sec "
            f"({duration * 1000:.2f}ms for {iterations:,} ops)"
        )

    asyncio.run(run_loop())


def benchmark_smart_bind(iterations: int = 50000) -> None:
    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "query_string": b"age=25",
    }

    async def dummy_receive() -> dict[str, Any]:
        return {
            "type": "http.request",
            "body": b'{"name": "Alice"}',
            "more_body": False,
        }

    async def run_loop() -> None:
        ctx = Context(scope=scope, receive=dummy_receive, send=None)
        # Pre-read body
        await ctx.body()

        # Attrs bind
        start = time.perf_counter()
        for _ in range(iterations):
            _ = await ctx.bind(UserAttrs)
        duration_attrs = time.perf_counter() - start
        ops_attrs = iterations / duration_attrs

        # Dataclass bind
        start = time.perf_counter()
        for _ in range(iterations):
            _ = await ctx.bind(UserDC)
        duration_dc = time.perf_counter() - start
        ops_dc = iterations / duration_dc

        print("\n--- 4. Smart ctx.bind() Benchmark ---")
        print(
            f"bind(AttrsClass):          {ops_attrs:,.0f} ops/sec "
            f"({duration_attrs * 1000:.2f}ms for {iterations:,} ops)"
        )
        print(
            f"bind(Dataclass):           {ops_dc:,.0f} ops/sec "
            f"({duration_dc * 1000:.2f}ms for {iterations:,} ops)"
        )

    asyncio.run(run_loop())


def benchmark_full_asgi_app_pipeline(iterations: int = 30000) -> None:
    app = App()

    @app.get("/users/:id")
    def get_user(ctx: Context) -> dict[str, Any]:
        return {
            "user_id": ctx.path_params.get("id"),
            "status": "active",
        }

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/users/1001",
        "headers": [(b"user-agent", b"bench")],
    }

    async def dummy_receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def dummy_send(message: dict[str, Any]) -> None:
        pass

    async def run_loop() -> None:
        start = time.perf_counter()
        for _ in range(iterations):
            await app(scope, dummy_receive, dummy_send)
        duration = time.perf_counter() - start
        ops_sec = iterations / duration

        print("\n--- 5. Full ASGI App Request Pipeline Benchmark ---")
        print(
            f"App.__call__ End-to-End:   {ops_sec:,.0f} req/sec "
            f"({duration * 1000:.2f}ms for {iterations:,} reqs)"
        )

    asyncio.run(run_loop())


def benchmark_security_and_sessions(iterations: int = 50000) -> None:
    secret = b"super-secret-key-12345"
    data = b'{"user_id": "1001", "role": "admin"}'

    # 1. Cookie Signing / Unsigning Benchmark
    start = time.perf_counter()
    for _ in range(iterations):
        cookie = _sign(data, secret)
        _ = _unsign(cookie, secret)
    duration_sig = time.perf_counter() - start
    ops_sig = iterations / duration_sig

    # 2. Rate Limiting Check Benchmark
    limiter = MemoryRateLimiter(requests_per_minute=1000000)
    start = time.perf_counter()
    for _ in range(iterations):
        _ = limiter.is_allowed("127.0.0.1")
    duration_rate = time.perf_counter() - start
    ops_rate = iterations / duration_rate

    print("\n--- 6. Security & Session Benchmark ---")
    print(
        f"Cookie Sign + Unsign:      {ops_sig:,.0f} ops/sec "
        f"({duration_sig * 1000:.2f}ms for {iterations:,} ops)"
    )
    print(
        f"Rate Limiter Check:        {ops_rate:,.0f} ops/sec "
        f"({duration_rate * 1000:.2f}ms for {iterations:,} ops)"
    )


def benchmark_response_normalization(iterations: int = 100000) -> None:
    payload = {"status": "ok", "items": [1, 2, 3, 4, 5]}

    start = time.perf_counter()
    for _ in range(iterations):
        _ = normalize_response(payload)
    duration = time.perf_counter() - start
    ops_sec = iterations / duration

    print("\n--- 7. Response Normalization Benchmark ---")
    print(
        f"normalize_response(dict):  {ops_sec:,.0f} ops/sec "
        f"({duration * 1000:.2f}ms for {iterations:,} ops)"
    )


if __name__ == "__main__":
    print("=" * 60)
    print("      LETTIA v1.0 BENCHMARK SUITE (Python 3.12 Engine)")
    print("=" * 60)
    benchmark_routing_performance()
    benchmark_context_allocation()
    benchmark_middleware_chain()
    benchmark_smart_bind()
    benchmark_full_asgi_app_pipeline()
    benchmark_security_and_sessions()
    benchmark_response_normalization()
    print("=" * 60)
