# Changelog

All notable changes to Lettia are documented here.

## [Unreleased]

- Continue improving the ASGI core, component-oriented documentation, and
  release automation.

## [1.0.0] - 2026-08-01

### Added

- ASGI application dispatch for HTTP, WebSocket, and lifespan scopes.
- Static, parameterized, wildcard, named, and method-aware routing.
- Pre-routing, global, route, and group middleware chains.
- Lazy request parsing, typed binding, response normalization, cookies, and
  streaming responses.
- Built-in recovery, CORS, request ID, timeout, body limit, rate limit, and
  signed-session middleware.
- Binder, validator, renderer, static-file, and HTTPX testing extensions.
- Behavioral tests, regression coverage, benchmarks, and component API docs.

### Quality

- 54 tests passing with an 80% coverage gate.
- Ruff, Pyright, compileall, package builds, Twine checks, and strict docs
  builds are part of the release validation workflow.
