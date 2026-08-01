# Contributing to Lettia

Lettia targets Python 3.12+ and uses `uv` for dependency management. Read
[`AGENTS.md`](AGENTS.md) for repository-specific conventions and commands.

## Development setup

```bash
uv sync --all-extras
uv run pytest
uv run ruff check .
uv run pyright
uv run zensical build --strict
```

Keep changes focused. Public behavior belongs in `src/lettia`; tests belong in
`tests/`; runnable examples belong in `examples/`; user documentation belongs
in `docs/`.

## Tests and documentation

Add regression coverage for bug fixes and test observable behavior, especially
routing, middleware order, request boundaries, response headers, static-file
security, sessions, and WebSocket state. Keep source coverage at or above the
80% gate. When adding a documentation page, update `zensical.toml` navigation
and run the strict build.

## Pull requests

Describe the behavior change, affected public APIs, and validation commands.
Include benchmark output for performance changes and document any compatibility
or security implications. Keep generated `site/` and `dist/` artifacts out of
feature commits unless the release process explicitly requires them.
