# Repository Guidelines

## Project Structure & Module Organization

- `src/lettia/` contains the typed ASGI framework core: `app.py`, routing,
  request `context.py`, responses, errors, and WebSockets.
- `src/lettia/middleware/`, `protocols/`, and `ext/` hold composable middleware,
  binding/validation/rendering contracts, and extensions such as `StaticFiles`.
- `tests/` contains behavior and regression tests; `examples/` contains runnable
  REST and static-site examples; `benchmarks/` contains local performance tests.
- `docs/` contains user guides and component-oriented API references. Update
  `zensical.toml` when adding documentation pages.

## Build, Test, and Development Commands

Use Python 3.12+ and `uv`:

```bash
uv sync
uv run pytest                         # tests with the 90% coverage gate
uv run pytest tests/unit/test_routing.py --no-cov    # focused tests
uv run ruff check .                   # lint
uv run pyright                        # type check
uv run python benchmarks/run_benchmark.py
uv run zensical build --strict        # documentation build
```

Run an example with `uv run python examples/rest_api.py`, or serve an ASGI app
with `uv run uvicorn app:app --reload`.

## Coding Style & Naming Conventions

Use four-space indentation, Python 3.12 type hints, and an 88-character line
limit. Ruff enforces E/W/F/I/B/C4/UP/ASYNC rules. Use `snake_case` for
functions, methods, modules, and variables; `PascalCase` for classes; and
uppercase names for constants. Prefer `attrs` classes with `slots=True` for
framework data objects, narrow exception handling, and explicit async ASGI
boundaries.

## Testing Guidelines

Name files `tests/<layer>/test_<component>.py` (unit, integration, smoke or
architecture), mark every test with a registered contract ID, and name tests
`test_<unit>_<scenario>_<expected_result>()`. Test observable contracts and
edge cases: routing precedence, middleware order, invalid input, headers,
streaming, traversal, sessions, and WebSocket disconnects. Add regression tests
for every bug fix and keep coverage at or above 90%.

## Commit & Pull Request Guidelines

Use concise imperative subjects, for example
`Fix HEAD fallback for GET routes`. Pull requests should describe behavior
changes, link relevant issues, list validation commands, and call out API or
documentation changes. Include benchmark results for performance work.

## Security & Configuration Tips

Do not commit secrets. Signed sessions provide integrity, not encryption; the
in-memory rate limiter is process-local. Treat forwarded proxy headers as
untrusted unless the deployment explicitly trusts the proxy.
