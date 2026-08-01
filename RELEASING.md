# Release Checklist

## Before tagging

- Confirm the version in `pyproject.toml` and `CHANGELOG.md` match.
- Confirm the MIT `LICENSE` file and corresponding `license` metadata are present.
- Add verified repository, documentation, and issue URLs to `pyproject.toml`.
- Review the public API and migration notes for the release.
- Run `uv lock --check` and `uv sync --locked --all-extras`.

## Validation

```bash
uv run pytest
uv run ruff check .
uv run pyright
uv run zensical build --strict
uv build
uvx --from twine twine check dist/*
```

Run `uv run python benchmarks/run_benchmark.py` when routing, middleware,
binding, response, or security code changes. Test the wheel in a clean target
environment before publishing.

## Publish

1. Commit the release changes and create an annotated `vX.Y.Z` tag.
2. Publish to TestPyPI and install the wheel with each supported Python version.
3. Publish to PyPI only after the TestPyPI smoke test passes.
4. Create release notes from `CHANGELOG.md` and verify documentation links.

Never publish with placeholder license or project URLs.
