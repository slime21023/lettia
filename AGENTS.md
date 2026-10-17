# Repository Guidelines

`spec/_Design.md` is the source of truth for scope and API shape. Do not edit it
unless asked; propose changes instead.

## Project Structure & Module Organization

- `src/lettia/core/` (L0/L1): ASGI adapter, `App`, `Group`, router, `Context`,
  `Key[T]`, `HTTPError`. Must not import `lettia.contract`.
- `src/lettia/middleware/` (L1): built-in middleware.
- `src/lettia/contract/` (L2): `op`, source markers, response classes, `GuardSpec`,
  `mount`, codec, OpenAPI derivation.
- `src/lettia/__init__.py`: public entry point re-exporting the common names.
- `src/lettia/testing/`, `src/lettia/cli/`: test client and `lettia openapi export`.
- `tests/typing/` (positive and negative fixtures plus snapshots), `tests/unit/`,
  `tests/integration/`; `benchmarks/` for S1/S2/S3.

Layers depend only downward. Do all parsing of annotations at registration or
mount time; request-time code only looks things up and calls.

## Build, Test, and Development Commands

Use Python 3.12+ and `uv`:

```bash
uv sync --all-extras
uv run pytest                              # unit, integration and typing tests
uv run pytest tests/typing -q              # typing tests only
uv run ruff check src tests && uv run ruff format --check src tests
uv run pyrefly check                       # primary type checker
uv run pyright && uv run mypy              # compatibility targets
```

Run `ruff` only on `src`, `tests` and `benchmarks`: ruff also formats code blocks in
Markdown, which rewrites `spec/`.

## Coding Style & Naming Conventions

Four-space indentation, Python 3.12 syntax (PEP 695 generics and `type` aliases), and
an 88-character line limit. `snake_case` for functions, methods, modules and
variables; `PascalCase` for classes; uppercase for constants. Use only typing forms
that pyrefly, pyright and mypy all check correctly. Prefer explicit, fixed handler
signatures `(self, c, req)`; never inject by name or by type. msgspec is the only
runtime dependency; keep `httpx` and similar tools out of the core import path.

## Testing Guidelines

Name files `tests/<layer>/test_<component>.py` and tests
`test_<unit>_<scenario>_<expected_result>()`. Cover observable contracts and edge
cases: routing precedence, middleware order, invalid input, guard composition,
startup checks, deterministic OpenAPI output.

Typing tests: `tests/typing/positive/` holds valid forms that all three checkers must
accept with zero errors; `negative/` holds one file per class of error. Snapshots in
`tests/typing/snapshots/` record `(line, code)` per checker. Regenerate with
`LETTIA_UPDATE_TYPING_SNAPSHOTS=1 uv run pytest tests/typing`, and explain any
change in the PR. A checker that cannot flag a case goes in `KNOWN_GAPS` with a reason.

## Commit & Pull Request Guidelines

Use concise imperative subjects, for example `Add radix router`. Keep each commit
single-purpose and green. Pull requests describe behavior changes, link issues, list
validation commands, and call out public API or design-document changes. Include
benchmark results for performance work.

## Security & Configuration Tips

Do not commit secrets. Treat forwarded proxy headers as untrusted unless the
deployment explicitly trusts the proxy. Guards read only the request head and run
before decoding, so unauthenticated requests never see field-level errors.
