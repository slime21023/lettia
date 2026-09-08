# Changelog

All notable changes to Lettia are documented here.

## [Unreleased]

- Continue improving the ASGI core, component-oriented documentation, and
  release automation.

## [1.0.1] - 2026-09-08

### Changed

- Made `Router` and `Route` generic so higher-level frameworks retain their
  handler type through route matching.
- Made `App` slots-based; application objects no longer accept dynamic
  attributes.
- Removed `Any` from framework source and consolidated dynamic binding and
  HTTPX adaptation at documented typed boundaries.
- Published handler contracts and migrated tests, examples, and API reference
  signatures to the same typed ASGI boundary.
- Added typed ASGI HTTP, WebSocket, lifespan, and JSON contracts under
  `lettia.asgi`.
- Added strict Pyrefly project configuration and public API coverage policy.

### Fixed

- Terminate streamed HTTP responses when an iterator fails after headers have
  been sent.

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

- 54 tests passing with an 80% coverage gate at the time of the 1.0.0 release.
- Ruff, Pyright, compileall, package builds, Twine checks, and strict docs
  builds are part of the release validation workflow.
