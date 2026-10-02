---
title: Testing
---

# Testing

Lettia supports three useful testing styles:

- `TestClient` for concise synchronous tests.

- `httpx.AsyncClient` with `ASGITransport` for async HTTP tests.

- Direct `app(scope, receive, send)` calls for lifespan and WebSocket protocol
  tests.

Install the development tools:

```bash
uv sync --locked --all-extras
```

## Synchronous route tests

`TestClient` wraps `httpx.ASGITransport` and exposes `get`, `post`, `put`,
`delete`, and `patch` helpers:

```python
from lettia import App, Context
from lettia.testing import TestClient

app = App()


@app.get("/users/:user_id")
def get_user(ctx: Context) -> dict[str, str]:
    return {"id": ctx.path_params["user_id"]}


def test_get_user() -> None:
    client = TestClient(app)

    response = client.get("/users/42")

    assert response.status_code == 200
    assert response.json() == {"id": "42"}
```

`TestClient` is synchronous and uses `asyncio.run()` internally. Use the async
style below when the test itself already runs inside an event loop.

## Async ASGI tests

```python
import httpx
import pytest


@pytest.mark.asyncio
async def test_echo() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post("/echo", json={"message": "hello"})

    assert response.status_code == 200
```

Use this style for streaming and async HTTP behavior. Test lifespan and
WebSocket protocol messages by calling the ASGI application with controlled
`scope`, `receive`, and `send` callables; HTTPX's ASGI transport is an HTTP
transport, not a WebSocket client.

## What to test

Organize tests around public behavior rather than private implementation:

- A route returns the expected status, headers, and body.

- Invalid input returns the documented HTTP error.

- Middleware preserves nesting order and error semantics.

- Route precedence, path parameters, 405/Allow, and HEAD behavior are stable.

- Static file traversal, range requests, cookies, and WebSocket disconnects
  are covered as boundary cases.

- The suite enforces combined statement and branch coverage, so conditional
  error paths remain part of the quality gate.

Name tests as `test_<unit>_<scenario>_<expected_result>()` so failures explain
the contract they protect.

## Property-based core tests

The core suite uses Hypothesis to generate inputs and shrink failures. Shared
bounded strategies in `tests/strategies.py` produce JSON, Unicode text, path
segments, and byte payloads. Properties check observable contracts against
simple independent oracles:

| Area | Generated inputs | Contract / oracle |
|---|---|---|
| Router and groups | Route insertion order, methods, Unicode paths, prefixes | Static/parameter/wildcard precedence, URL round trips, HEAD/Allow |
| Context | Chunked bytes, limits, repeated headers/query fields | Concatenation, cache-independent limits, JSON round trips |
| Binding and rendering | Scalars, nulls, wrong types, HTML-looking text | Field types/defaults, JSON precedence, parsed HTML text |
| Responses | Bodies, statuses, chunk sequences, source/send failures | One ASGI start and terminal body, payload conservation, iterator cleanup |
| Middleware | Chain length, failure scope, CORS tokens, nested limits | Nesting order, one error rendering/log, preserved outer headers |
| Sessions | JSON data, signed expiry boundaries, malformed envelopes | Authenticity, expiry, future rejection, legacy invalidation |
| Rate limiter | Keys and clock advances | Independent list-based sliding-window model |
| State | Set/get/discard operation sequences | Independent mapping keyed by key identity and isolated stores |
| Static files | Bytes, ranges, changed content with unchanged metadata | Python byte slices, content-sensitive ETag, final path containment |
| WebSockets | Accept/send/close operation sequences | Connecting/connected/closed reference model |

Public API, documentation, lifespan, optional-dependency, and integration smoke
tests remain explicit examples. Remove an old example only when its behavior
is covered by a property; retain minimal bug reproducers as `@example` cases
or focused regression tests.

### Run and reproduce

Hypothesis is already a development dependency. Use `uv sync --locked --all-extras`
and the usual `uv run pytest`; no separate test runner or property profile is
required. Normal runs use Hypothesis defaults and its local example database.

~~~bash
uv run pytest tests/test_router_properties.py --hypothesis-show-statistics
uv run pytest tests/test_response.py -k stream --hypothesis-seed=12345
~~~

When a failure occurs, retain the falsifying example and seed/reproduction
information from the output. Replay the same test and seed on the same dependency
versions, then promote the smallest relevant failure to a permanent regression.
Do not pin the whole suite to one seed. The local `.hypothesis/` database is
ignored by Git.

Mutable state, clocks, apps, and temporary directories are created inside each
generated example. Session/rate-limit tests control clocks; timeout tests wait
on events rather than racing sleeps. File properties disable Hypothesis's
per-example deadline for filesystem variability; pure properties keep the
default deadline and health checks. Linux must run symlink containment tests;
Windows skips them only if symlink privileges are unavailable.

CI already runs the suite on Ubuntu and Windows with Python 3.12, 3.13, and
3.14. The same 90% statement/branch coverage gate applies after migration.

## Coverage and quality gates

Run the complete development loop:

```bash
uv run pytest
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pyrefly check
uv run pyrefly coverage check src/lettia --strict --public-only --fail-under 100
uv run zensical build --strict
```

Pytest is configured to print missing lines and fail below 90% source coverage.
For a focused report:

```bash
uv run pytest tests/test_regressions.py --cov=lettia --cov-report=term-missing
```

Coverage is a diagnostic, not a target by itself. Prefer tests that protect
observable behavior and edge conditions over tests written only to execute a
line.


## Migration validation snapshot

On 2026-10-02, the Windows Python 3.12.14 suite increased from 98 passing
tests and 90.16% coverage to 114 passing tests and 92.21% coverage, with one
symlink-privilege skip. Python 3.13 and 3.14 also passed the full Windows suite.
Linux Python 3.12 and 3.13 passed all 115 tests, including symlink containment,
with 92.31% coverage.
Strict public API type coverage remains 100%.

The initial test-only migration reproduced 17 failing properties before source
changes. A later deadline-during-send property reproduced one additional
streaming failure before its fix.

Local benchmark observations (Windows, Python 3.12.14):

| Operation | Before (one run, ops/s) | After (median of three runs, ops/s) |
|---|---:|---:|
| attrs binding | 167,490 | 158,436 |
| dataclass binding | 161,854 | 148,426 |
| Full ASGI request | 186,995 | 180,942 |

These are indicative local timings, not a controlled statistical comparison.
Binding now validates the schema and scalar values; response dispatch now
preserves middleware error boundaries and iterator cleanup.

Warm-filesystem conditional static requests, which include content hashing,
took medians of 0.642 ms (1 KiB), 0.959 ms (1 MiB), and 3.947 ms (8 MiB) across
three runs of 20 requests per size. Larger files incur proportionally more
reading. Re-run `benchmarks/run_benchmark.py` on the deployment filesystem.
