---
title: Testing
---

# Testing

Choose a test transport according to the behavior being checked:

- `TestClient` for concise synchronous tests.

- `httpx.AsyncClient` with `ASGITransport` for async HTTP tests.

- Direct `app(scope, receive, send)` calls for lifespan and WebSocket protocol
  tests, or controlled HTTP send/receive failures.

- A real Uvicorn server and HTTPX client for socket disconnects, connection
  reuse and server startup/shutdown; see [smoke tests](#real-server-smoke-tests).

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

Use this style for application HTTP behavior. In-process transports can buffer
streams and cannot verify real client disconnects; use the socket smoke suite
for those boundaries. HTTPX ASGITransport does not run lifespan automatically.
Test lifespan and WebSocket protocol messages by calling the ASGI application with controlled
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
bounded strategies in `tests/support/strategies.py` produce JSON, Unicode text, path
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
uv run pytest tests/unit/test_routing.py --no-cov --hypothesis-show-statistics
uv run pytest tests/unit/test_stream_lifecycle.py --no-cov --hypothesis-seed=12345
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

CI is configured to run the suite on Ubuntu and Windows with Python 3.12, 3.13,
and 3.14. Configuration is distinct from a completed run: verify the commit's
actual CI status before release. The same 90% statement/branch coverage gate
applies on each matrix entry.

## Coverage and quality gates

The repository suite uses a responsibility-based test pyramid:

| Directory / marker | Purpose | Examples |
|---|---|---|
| `tests/unit` / `unit` | Pure input partitions and one stateful owner with controlled events | Header atomicity, scalar coercion, Writer transitions, request cache |
| `tests/integration` / `integration` | Real component handoffs | Error replacement, policies, binding 400, file delivery and mount paths |
| `tests/smoke` / `smoke` | Real network and server lifetime | HEAD, mid-stream disconnect, startup and shutdown |
| `tests/architecture` / `architecture` | Dependency, API, documentation and collection policies | No Context dependency in JSON consumers; no Writer flag access from App |

Shared typed transports, strategies and the server harness live in
`tests/support/`. Import them explicitly through `tests.support`; do not add
test directories to the import path. Tests that need the repository root must
derive it from their current file location, not their former flat layout.

### Contracts and TDD

`tests/contracts.toml` records each stable contract ID, owner, rule, inputs,
outputs, errors, side effects and required layers. Every test function carries
at least one `@pytest.mark.contract("ID")`. Directory placement supplies the
layer marker automatically. Unknown IDs, missing contracts, unclassified
tests and manually assigned layer markers fail collection. Full-tree collection
also checks required contract layers before marker/name filtering; a focused
file cannot validate the entire inventory.

```bash
uv run pytest --collect-only -q
uv run pytest -m unit --no-cov
uv run pytest -m integration --no-cov
uv run pytest -m smoke --no-cov
uv run pytest -m architecture --no-cov
uv run pytest tests/unit/test_binding_rules.py --no-cov
```

The report separates test functions, expanded parameter cases and covered
contracts. Hypothesis examples are generated within one case and do not inflate
case counts. There is no target unit/integration ratio or minimum case count.

For a behavior change, select the contract and lowest responsible layer, add a
minimal failing example, implement the correction, then refactor. Add an
integration test only when a component handoff changes. Run the affected unit
tests, neighboring integration tests and the complete stage gate. A structural
refactor uses the existing passing suite as its baseline; do not manufacture a
behavior failure. If a defect is discovered, record its failing regression and
fix it separately from the structural move.

Use events to control send, cancellation and cleanup boundaries. Keep tests for
transport failure, deadline expiry, repeated cancellation and cleanup conflicts
even when their individual components also have unit coverage. Property tests
use independent expected values, and minimal historical reproducers remain
explicit. Removing a matrix member requires a recorded original case,
replacement test, contract and retained boundary risk; see the
[audit](testing_audit.md) and `tests/migration.json`.

### Real server smoke tests

The default full suite includes real Uvicorn/HTTPX tests. The test harness binds
an IPv4 loopback socket to port zero before starting Uvicorn in a managed thread,
waits for server startup with an event and uses an HTTP client with bounded
timeouts and environment proxies disabled. It closes the client, requests
graceful shutdown, joins the thread and closes the socket even when a test fails.
Startup and shutdown waits are bounded; forced shutdown is reported as a failure.

The HEAD test reuses the connection for GET to detect stray body bytes. The
stream test closes a real client connection after the first chunk and waits for
source cleanup, then verifies background work was skipped. Lifespan tests check
both ordered startup/shutdown and cleanup after startup or client failures.
These tests do not rely on ASGITransport or assume HTTPX runs lifespan.

### Verification commands

This is the canonical command list for repository development. Install the
locked development environment and optional packages first:

```bash
uv sync --locked --all-extras
```

Run the quality gates:

```bash
uv run python -m pytest
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pyrefly check
uv run pyrefly coverage check src/lettia --strict --public-only --fail-under 100
uv run zensical build --strict
```

For an additional ty check without changing project dependencies, use the
verified tool version with the same source, test and example scope:

```bash
uvx ty@0.0.84 check src tests examples --python .venv --python-version 3.12 --error-on-warning
uv run pyrefly check --min-severity warn
```

The ty command uses the project's installed packages. Pyrefly uses the strict
project configuration; the second command also displays warning diagnostics.

| Check | Scope | Where enforced |
|---|---|---|
| Pytest and Ruff | Full test pyramid; project formatting and lint | CI quality matrix |
| Pyright | `src`, strict, Python 3.12 target | CI quality matrix |
| Pyrefly | `src`, `tests`, `examples`, strict, Python 3.12 target | CI quality matrix |
| Public API type coverage | `src/lettia`, strict public-only, 100% | CI quality matrix |
| ty 0.0.84 | `src`, `tests`, `examples`, Python 3.12 target, warnings fail | Additional local check; not installed or enforced by current CI |
| Strict documentation build | Pages and configured navigation | CI quality matrix |

Type-checker target versions describe static analysis; runtime tests use the
selected matrix interpreter. Package validation follows the quality jobs:

```bash
uv build
uvx --from twine twine check dist/*
```

Before release, verify the intended version's wheel and source distribution.
Older files in a local `dist` directory are not evidence for the current build.

Pytest is configured to print missing lines and fail below 90% source coverage.
For focused feedback without the whole-project coverage gate:

```bash
uv run pytest tests/integration/test_error_policies.py --no-cov
```

Coverage is a diagnostic, not a target by itself. Prefer tests that protect
observable behavior and edge conditions over tests written only to execute a
line.


## Validation records

The [contract audit](testing_audit.md#verification-status) records the most
recent local verification, tool versions and outstanding checks. Test counts
are snapshots, not targets; use pytest's collection summary for the current
checkout. Historical results below do not establish that a later commit passed
the same platforms.

### Historical property-test migration

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
