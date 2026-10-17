"""Benchmark through a real server: one uvicorn worker, real HTTP connections.

This is the measurement the design's performance goals refer to. For every
scenario the apps to compare are started side by side, and load is applied to
them in turn for several rounds, so drift of the machine does not favour the app
measured first. The load comes from separate processes (keep-alive connections,
pipelining off) and shares the machine with the server, so absolute numbers
depend on the hardware; compare the ratios.

    uv run python -m benchmarks.run_server_benchmark [--seconds 5] [--rounds 3]
"""

import argparse
import asyncio
import socket
import statistics
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

BODY = b'{"name": "Ann"}'
S3_REQUEST = (
    b"POST /users HTTP/1.1\r\nHost: bench\r\nContent-Type: application/json\r\n"
    b"Authorization: Bearer good\r\nContent-Length: "
    + str(len(BODY)).encode()
    + b"\r\n\r\n"
    + BODY
)


@dataclass(frozen=True)
class Scenario:
    title: str
    request: bytes
    ok_status: int
    apps: dict[str, str]  # label -> attribute of benchmarks.apps
    reference: str


SCENARIOS = [
    Scenario(
        "S1 static route, plain text",
        b"GET / HTTP/1.1\r\nHost: bench\r\n\r\n",
        200,
        {"lettia": "lettia_app", "starlette": "starlette_app"},
        "starlette",
    ),
    Scenario(
        "S2 path parameter, JSON",
        b"GET /users/42 HTTP/1.1\r\nHost: bench\r\n\r\n",
        200,
        {"lettia": "lettia_app", "starlette": "starlette_app"},
        "starlette",
    ),
    Scenario(
        "S3 contract: guard, decode body, JSON response",
        S3_REQUEST,
        201,
        {"hand-written Core": "handwritten_app", "contract": "lettia_app"},
        "hand-written Core",
    ),
]


# --- load generation (runs in separate processes) --------------------------


async def _connection(
    port: int, request: bytes, ok_status: int, start_counting: float, stop: float
) -> tuple[int, int]:
    ok = errors = 0
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        while (now := time.perf_counter()) < stop:
            writer.write(request)
            head = await reader.readuntil(b"\r\n\r\n")
            lowered = head.lower()
            marker = lowered.find(b"content-length:")
            length = 0
            if marker >= 0:
                length = int(lowered[marker + 15 : lowered.find(b"\r\n", marker)])
            if length:
                await reader.readexactly(length)
            if now >= start_counting:
                if int(head[9:12]) == ok_status:
                    ok += 1
                else:
                    errors += 1
    finally:
        writer.close()
    return ok, errors


async def _drive(
    port: int,
    request: bytes,
    ok_status: int,
    connections: int,
    seconds: float,
    warmup: float,
) -> tuple[int, int]:
    start = time.perf_counter()
    counting = start + warmup
    stop = counting + seconds
    results = await asyncio.gather(
        *(
            _connection(port, request, ok_status, counting, stop)
            for _ in range(connections)
        )
    )
    return sum(r[0] for r in results), sum(r[1] for r in results)


def _worker(
    port: int,
    request: bytes,
    ok_status: int,
    connections: int,
    seconds: float,
    warmup: float,
) -> tuple[int, int]:
    return asyncio.run(_drive(port, request, ok_status, connections, seconds, warmup))


# --- servers ---------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _start_server(attribute: str, port: int) -> subprocess.Popen[bytes]:
    process = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-m",
            "uvicorn",
            f"benchmarks.apps:{attribute}",
            "--port",
            str(port),
            "--log-level",
            "warning",
            "--no-access-log",
        ]
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
            return process
        except OSError:
            if process.poll() is not None:
                raise RuntimeError(f"uvicorn for {attribute} exited early") from None
            time.sleep(0.2)
    process.kill()
    raise RuntimeError(f"uvicorn for {attribute} did not start")


def _requests_per_second(
    pool: ProcessPoolExecutor, port: int, scenario: Scenario, args: argparse.Namespace
) -> tuple[float, int]:
    futures = [
        pool.submit(
            _worker,
            port,
            scenario.request,
            scenario.ok_status,
            args.connections,
            args.seconds,
            args.warmup,
        )
        for _ in range(args.clients)
    ]
    results = [future.result() for future in futures]
    ok = sum(r[0] for r in results)
    return ok / args.seconds, sum(r[1] for r in results)


def run_scenario(
    pool: ProcessPoolExecutor, scenario: Scenario, args: argparse.Namespace
) -> None:
    ports = {label: _free_port() for label in scenario.apps}
    servers = {
        label: _start_server(attribute, ports[label])
        for label, attribute in scenario.apps.items()
    }
    rates: dict[str, list[float]] = {label: [] for label in scenario.apps}
    errors = 0
    try:
        for _ in range(args.rounds):
            for label in scenario.apps:
                rate, failed = _requests_per_second(pool, ports[label], scenario, args)
                rates[label].append(rate)
                errors += failed
    finally:
        for server in servers.values():
            server.terminate()
        for server in servers.values():
            server.wait(timeout=10)

    print(f"\n{scenario.title}")
    best = {label: max(values) for label, values in rates.items()}
    for label, values in rates.items():
        ratio = best[label] / best[scenario.reference]
        print(
            f"  {label:<20} best {best[label]:>8,.0f} req/s  "
            f"median {statistics.median(values):>8,.0f}  "
            f"({ratio:>4.2f}x {scenario.reference})"
        )
    if "contract" in best:
        overhead = 1 - best["contract"] / best[scenario.reference]
        print(f"  contract overhead: {overhead:.0%} (target <= 25%)")
    if errors:
        print(f"  WARNING: {errors} responses had an unexpected status")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--warmup", type=float, default=1.0)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument(
        "--connections", type=int, default=16, help="per client process"
    )
    parser.add_argument("--clients", type=int, default=2, help="load processes")
    args = parser.parse_args()
    print(
        f"one uvicorn worker, {args.clients} load processes x {args.connections} "
        f"connections, {args.rounds} interleaved rounds of {args.seconds:g}s"
    )
    with ProcessPoolExecutor(max_workers=args.clients) as pool:
        for scenario in SCENARIOS:
            run_scenario(pool, scenario, args)


if __name__ == "__main__":
    main()
